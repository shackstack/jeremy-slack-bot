import requests
from datetime import datetime

import os
SUBWAY_API_KEY = os.environ.get("SUBWAY_API_KEY", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# ===================== 설정 =====================
DEPART_STATION = "충무로"   # 출발역
ARRIVE_STATION = "신사"     # 하차역
COMMUTE_LINE = "1003"       # 3호선
COMMUTE_DIRECTION = "대화"  # 신사역 방향 (대화방면)
STOPS_TO_SINSA = 8          # 충무로 → 신사 정거장 수
# ================================================
 
SUBWAY_LINE_MAP = {
    "1001": "1호선", "1002": "2호선", "1003": "3호선", "1004": "4호선",
    "1005": "5호선", "1006": "6호선", "1007": "7호선", "1008": "8호선",
    "1009": "9호선", "1061": "중앙선", "1063": "경의중앙선", "1065": "공항철도",
    "1067": "경춘선", "1075": "수인분당선", "1077": "신분당선",
    "1092": "우이신설선", "1093": "서해선",
}
 
AVG_MINUTES_PER_STOP = 2  # 정거장당 평균 소요 시간 (분)
AVG_SECONDS_PER_STOP = AVG_MINUTES_PER_STOP * 60
WALK_TO_STATION_SEC = 5 * 60  # 회사 → 역 도보 시간 (5분)

# 충무로 → 대화 방향 상류역 (가까운 순)
UPSTREAM_STATIONS = [
    "약수", "금호", "옥수", "압구정", "신사", "잠원", "고속터미널",
    "교대", "남부터미널", "양재", "매봉", "도곡", "구정", "개나리", "일원", "수서",
]
LINE3_STATIONS = [DEPART_STATION, *UPSTREAM_STATIONS]
SCAN_UNTIL = "고속터미널"  # 2편 찾을 때까지 최소 스캔 범위
 
 
def get_subway_arrivals(station: str) -> list:
    url = (
        f"http://swopenapi.seoul.go.kr/api/subway/"
        f"{SUBWAY_API_KEY}/json/realtimeStationArrival/0/10/{station}"
    )
    try:
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        data = res.json()
 
        if "errorMessage" in data:
            err = data["errorMessage"]
            if int(err.get("status", 200)) != 200:
                print(f"API 오류: {err.get('message')}")
                return []
 
        return data.get("realtimeArrivalList", [])
    except requests.RequestException as e:
        print(f"요청 오류: {e}")
        return []
 
 
def is_my_train(item: dict) -> bool:
    return (
        item.get("subwayId") == COMMUTE_LINE
        and COMMUTE_DIRECTION in item.get("trainLineNm", "")
    )


def estimate_eta_at_depart(train: dict, observed_station: str) -> int:
    """관측 역 기준 barvlDt → 출발역 도착까지 남은 초 추정"""
    try:
        wait_sec = int(train.get("barvlDt") or 0)
    except ValueError:
        wait_sec = 0

    try:
        depart_idx = LINE3_STATIONS.index(DEPART_STATION)
        observed_idx = LINE3_STATIONS.index(observed_station)
    except ValueError:
        return wait_sec

    stops = observed_idx - depart_idx
    return wait_sec + stops * AVG_SECONDS_PER_STOP


def format_eta_message(eta_sec: int) -> str:
    if eta_sec < 60:
        return "곧 도착"
    mins, secs = divmod(eta_sec, 60)
    if secs == 0:
        return f"{mins}분 후"
    return f"{mins}분 {secs}초 후"


NEAREST_TRAIN_COUNT = 3


def collect_nearest_trains() -> list[dict]:
    """현재 시각 기준 출발역 도착이 가장 가까운 열차 최대 3편 수집"""
    candidates: dict[str, dict] = {}
    scan_until_idx = LINE3_STATIONS.index(SCAN_UNTIL)

    for idx, station in enumerate(LINE3_STATIONS):
        for item in get_subway_arrivals(station):
            if not is_my_train(item):
                continue

            btrain_no = item.get("btrainNo")
            if not btrain_no:
                continue

            eta_sec = estimate_eta_at_depart(item, station)
            prev = candidates.get(btrain_no)
            if prev is None or eta_sec < prev["eta_sec"]:
                arvl_msg = (
                    item.get("arvlMsg2", "")
                    if station == DEPART_STATION
                    else f"약 {format_eta_message(eta_sec)}"
                )
                candidates[btrain_no] = {
                    "train": item,
                    "eta_sec": eta_sec,
                    "arvl_msg": arvl_msg,
                }

        if idx >= scan_until_idx and len(candidates) >= NEAREST_TRAIN_COUNT:
            break

    return sorted(candidates.values(), key=lambda t: t["eta_sec"])[:NEAREST_TRAIN_COUNT]
 
 
def format_personal_message(my_trains: list[dict]) -> dict:
    now = datetime.now().strftime("%H:%M")
    today = datetime.now().strftime("%m월 %d일")
 
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🏃 퇴근할 시간이에요!", "emoji": True}
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"📅 {today}  🕡 {now} 기준  |  {DEPART_STATION} → {ARRIVE_STATION}역 (3호선 {COMMUTE_DIRECTION}방면)"}]
        },
        {"type": "divider"}
    ]
 
    if not my_trains:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"⚠️ *3호선 {COMMUTE_DIRECTION}방면* 열차 도착 정보가 없어요.\n"
                    "잠시 후 다시 확인해보세요."
                ),
            },
        })
        return {"text": "퇴근길 지하철 정보 없음", "blocks": blocks}

    labels = ["⬆️ 1번째 열차", "⏭️ 2번째 열차", "⏭️ 3번째 열차"]
    for i, entry in enumerate(my_trains):
        arvl_msg = entry["arvl_msg"]

        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*{labels[i]}*"},
                {"type": "mrkdwn", "text": f"*⏱ {arvl_msg}*"},
            ]
        })

        if i < len(my_trains) - 1:
            blocks.append({"type": "divider"})
 
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                f"🚉 {DEPART_STATION} → 압구정 → {ARRIVE_STATION} "
                f"(약 {STOPS_TO_SINSA}정거장 · {STOPS_TO_SINSA * AVG_MINUTES_PER_STOP}분 소요)  "
                f"|  출발역 도착 예상 시간 가까운 순 {NEAREST_TRAIN_COUNT}편"
            ),
        }]
    })
 
    return {
        "text": f"🏃 퇴근 알림 — {DEPART_STATION}역에서 {COMMUTE_DIRECTION}방면 탑승하세요! ({now})",
        "blocks": blocks
    }
 
 
def send_to_slack(payload: dict) -> bool:
    try:
        res = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=5)
        if res.status_code == 200:
            print("✅ Slack 전송 성공!")
            return True
        else:
            print(f"❌ Slack 전송 실패: {res.status_code} {res.text}")
            return False
    except requests.RequestException as e:
        print(f"❌ Slack 요청 오류: {e}")
        return False
 
 
if __name__ == "__main__":
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {DEPART_STATION}역 → {ARRIVE_STATION}역 퇴근 알림 조회 중...")
    my_trains = collect_nearest_trains()
    payload = format_personal_message(my_trains)
    send_to_slack(payload)
 