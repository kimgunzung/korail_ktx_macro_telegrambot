"""Status text shared by the CLI (`korailbot status`) and the KakaoTalk chatbot (`상태`)."""
from datetime import datetime
from typing import List

from config.settings import settings


def status_lines(storage, reservation_service, kakao, user_id: int = settings.LOCAL_USER_ID,
                 for_chat: bool = False) -> List[str]:
    """Build the status report.

    Args:
        for_chat: KakaoTalk wording (chat commands instead of CLI commands, shorter lines)
    """
    paid_hint = "결제 후 '결제완료'를 보내세요" if for_chat else "결제 후 `korailbot paid` 를 실행하세요"
    lines: List[str] = []

    running = reservation_service.running()
    if running:
        elapsed = datetime.now() - running.started_at
        lines.append(f"🔍 예약 검색 진행 중 ({int(elapsed.total_seconds() // 60)}분 경과"
                     + ("" if for_chat else f", pid={running.process_id}") + ")")
        lines.extend(("" if for_chat else "   ") + line for line in running.search_params.summary_lines())
        lines.append(("" if for_chat else "   ") + f"👤 계정: {running.korail_id}")
        seat = storage.get_current_seat_index(user_id)
        if seat is not None:
            lines.append(("" if for_chat else "   ") + f"💳 {seat + 1}번째 좌석 결제 대기 중 → {paid_hint}")
    else:
        lines.append("ℹ️ 진행 중인 예약 검색이 없습니다.")

    last = storage.get_last_result(user_id)
    if last:
        label = {"success": "✅ 성공", "failed": "❌ 실패", "partial": "⏳ 진행", "cancelled": "🚫 취소"}.get(last.status, last.status)
        lines.append("")
        lines.append(f"마지막 결과 ({last.reported_at.strftime('%m/%d %H:%M')}): {label}" + (" · 결제 완료" if last.paid else ""))
        body = last.message.strip()
        if for_chat and len(body) > 300:
            body = body[:300] + "…"
        lines.append(body if for_chat else "   " + body.replace("\n", "\n   "))

    payment = storage.get_payment_status(user_id)
    if payment and payment.reminder_active and not payment.completed:
        lines.append("")
        lines.append(f"⏰ 결제 리마인더 동작 중 — {paid_hint}.")

    multi = storage.get_multi_reservation_status(user_id)
    if multi and multi.reservations:
        lines.append("")
        lines.append("🪑 랜덤 배치 좌석 현황")
        for r in sorted(multi.reservations, key=lambda x: x.seat_number):
            state = {"pending": f"⏳ 결제 대기 (남은 시간 {r.get_remaining_minutes_display()})",
                     "paid": "✅ 결제 완료", "expired": "❌ 만료", "cancelled": "🚫 취소"}[r.status.value]
            lines.append(("" if for_chat else "   ") + f"좌석 {r.seat_number}: {state}")

    credentials = storage.get_credentials(user_id)
    card = storage.get_saved_card(user_id)
    lines.append("")
    lines.append(f"👤 계정: {credentials.korail_id if credentials else '없음 (korailbot login)'}")
    lines.append(f"💳 카드: {card.masked if card and card.is_complete else '없음 (korailbot card set)'}")
    if kakao.enabled:
        linked = kakao.link_summary(user_id)
        lines.append(f"📣 카카오톡 알림: {'연동됨 (' + linked + ')' if linked else ('미연동 (\"연동\" 입력)' if for_chat else '미연동 (korailbot kakao link)')}")
    else:
        lines.append("📣 카카오톡 알림: 서버 미설정 (KAKAO_REST_API_KEY)")
    return lines
