"""Input validation utilities."""
import re
from datetime import datetime
from typing import Tuple, Optional


class InputValidator:
    """Validator for user inputs in the reservation flow."""

    @staticmethod
    def validate_phone_number(phone: str) -> Tuple[bool, Optional[str]]:
        """
        Validate Korean phone number format with enhanced security.

        Args:
            phone: Phone number string

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not phone:
            return False, "전화번호를 입력해주세요."

        # Trim whitespace
        phone = phone.strip()

        if not phone:
            return False, "전화번호를 입력해주세요."

        # Check for suspicious patterns (SQL injection, script injection, etc.)
        suspicious_patterns = ['<', '>', ';', '--', '/*', '*/', 'script', 'SELECT', 'DROP', 'INSERT', 'UPDATE', 'DELETE']
        if any(pattern.lower() in phone.lower() for pattern in suspicious_patterns):
            return False, "유효하지 않은 문자가 포함되어 있습니다."

        # Check for minimum length
        if len(phone) < 10:
            return False, "전화번호가 너무 짧습니다."

        # Check for maximum length
        if len(phone) > 13:
            return False, "전화번호가 너무 깁니다."

        if "-" not in phone:
            return False, "'-'를 포함한 전화번호를 입력해주세요. (예: 010-1234-5678)"

        # Pattern: 010-xxxx-xxxx or 01x-xxx-xxxx or 01x-xxxx-xxxx
        pattern = r'^01[0-9]-\d{3,4}-\d{4}$'
        if not re.match(pattern, phone):
            return False, "올바른 전화번호 형식이 아닙니다. (예: 010-1234-5678)"

        # Additional validation: check that only digits and hyphens are present
        clean_phone = phone.replace('-', '')
        if not clean_phone.isdigit():
            return False, "전화번호는 숫자와 '-'만 포함해야 합니다."

        return True, None

    @staticmethod
    def validate_date(date_str: str) -> Tuple[bool, Optional[str]]:
        """
        Validate date in YYYYMMDD format with enhanced validation.

        Args:
            date_str: Date string

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not date_str:
            return False, "날짜를 입력해주세요."

        # Trim whitespace
        date_str = date_str.strip()

        if not date_str:
            return False, "날짜를 입력해주세요."

        # Check for non-digit characters
        if not date_str.isdigit():
            return False, "날짜는 숫자만 입력해주세요. (예: 20250101)"

        # Check length
        if len(date_str) != 8:
            return False, "날짜는 8자리로 입력해주세요. (예: 20250101)"

        # Check if date is valid
        try:
            year = int(date_str[:4])
            month = int(date_str[4:6])
            day = int(date_str[6:8])

            # Validate year range (reasonable range: 2020-2100)
            if year < 2020 or year > 2100:
                return False, f"연도가 유효하지 않습니다. (입력: {year}년)"

            # Validate month
            if month < 1 or month > 12:
                return False, f"월이 유효하지 않습니다. (입력: {month}월)"

            # Validate day
            if day < 1 or day > 31:
                return False, f"일이 유효하지 않습니다. (입력: {day}일)"

            # Parse date to check if it's valid (e.g., Feb 30 would fail)
            datetime(year, month, day)

        except ValueError as e:
            return False, f"유효하지 않은 날짜입니다. (예: 2월 30일은 존재하지 않습니다)"

        # Check if date is not in the past
        today = datetime.today().strftime("%Y%m%d")
        if date_str < today:
            return False, "과거 날짜는 선택할 수 없습니다."

        # Check if date is too far in the future (e.g., more than 1 year ahead)
        from datetime import timedelta
        max_future_date = (datetime.today() + timedelta(days=365)).strftime("%Y%m%d")
        if date_str > max_future_date:
            return False, "예매 가능한 기간을 초과했습니다. (최대 1년 이내)"

        return True, None

    @staticmethod
    def validate_time(time_str: str) -> Tuple[bool, Optional[str]]:
        """
        Validate time in HHMM format with enhanced validation.

        Args:
            time_str: Time string

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not time_str:
            return False, "시간을 입력해주세요."

        # Trim whitespace
        time_str = time_str.strip()

        if not time_str:
            return False, "시간을 입력해주세요."

        # Check for non-digit characters
        if not time_str.isdigit():
            return False, "시간은 숫자만 입력해주세요. (예: 1430은 14시 30분)"

        # Check length
        if len(time_str) != 4:
            return False, "시간은 4자리로 입력해주세요. (예: 1430은 14시 30분)"

        try:
            hours = int(time_str[:2])
            minutes = int(time_str[2:4])
        except ValueError:
            return False, "시간 형식이 올바르지 않습니다. (예: 1430)"

        # Validate hours
        if hours < 0 or hours > 23:
            return False, f"시간은 00-23 사이여야 합니다. (입력: {hours}시)"

        # Validate minutes
        if minutes < 0 or minutes > 59:
            return False, f"분은 00-59 사이여야 합니다. (입력: {minutes}분)"

        return True, None

    @staticmethod
    def validate_station_name(station: str) -> Tuple[bool, Optional[str]]:
        """
        Validate station name against actual Korail station database.

        Args:
            station: Station name

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Import here to avoid circular dependency
        from utils.station_codes import is_valid_station, get_similar_stations, format_station_suggestions

        if not station:
            return False, "역 이름을 입력해주세요."

        # Trim whitespace
        station = station.strip()

        if not station:
            return False, "역 이름을 입력해주세요."

        # Check for '역' suffix
        if "역" in station:
            return False, "'역'을 제외한 이름을 입력해주세요. (예: 광명)"

        # Check minimum length
        if len(station) < 2:
            return False, "역 이름이 너무 짧습니다. 최소 2자 이상 입력해주세요."

        # Check maximum length (reasonable limit)
        if len(station) > 10:
            return False, "역 이름이 너무 깁니다. 올바른 역 이름을 입력해주세요."

        # Check for invalid characters
        if not station.replace(" ", "").replace("-", "").isalnum():
            # Allow Korean, numbers, spaces, and hyphens only
            if not all(c.isalnum() or c in [' ', '-', 'ㄱ', 'ㄴ', 'ㄷ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅅ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ'] for c in station):
                return False, "역 이름에 특수문자를 사용할 수 없습니다."

        # Check against actual station database
        if not is_valid_station(station):
            # Get similar stations for suggestion
            similar = get_similar_stations(station)
            suggestion_text = format_station_suggestions(similar)

            error_msg = f"'{station}'은(는) 존재하지 않는 역입니다.{suggestion_text}"
            return False, error_msg

        return True, None

    @staticmethod
    def validate_yes_no(answer: str) -> Tuple[bool, Optional[str]]:
        """
        Validate yes/no answer with enhanced security.

        Args:
            answer: User's answer

        Returns:
            Tuple of (is_yes, None) or (False, error_message)
        """
        if not answer:
            return None, "Y/예 또는 N/아니오를 입력해주세요."

        # Trim and convert to uppercase
        answer = answer.strip().upper()

        if not answer:
            return None, "Y/예 또는 N/아니오를 입력해주세요."

        # Check length (prevent long inputs)
        if len(answer) > 10:
            return None, "입력이 너무 깁니다. Y/예 또는 N/아니오를 입력해주세요."

        # Valid positive responses
        if answer in ["Y", "예", "YES", "네", "ㅇ"]:
            return True, None
        # Valid negative responses
        elif answer in ["N", "아니오", "NO", "아니요", "ㄴ"]:
            return False, None
        else:
            return None, "Y/예 또는 N/아니오를 입력해주세요."

    @staticmethod
    def validate_train_type_choice(choice: str) -> Tuple[bool, Optional[str]]:
        """
        Validate train type choice (1 or 2) with enhanced validation.

        Args:
            choice: User's choice

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not choice:
            return False, "열차 종류를 선택해주세요. (1: KTX만, 2: 전체)"

        # Trim whitespace
        choice = choice.strip()

        if not choice:
            return False, "열차 종류를 선택해주세요. (1: KTX만, 2: 전체)"

        # Check if it's a digit
        if not choice.isdigit():
            return False, "숫자를 입력해주세요. (1 또는 2)"

        # Validate choice
        if choice not in ["1", "2"]:
            return False, "1 또는 2를 입력해주세요. (1: KTX만, 2: 전체)"

        return True, None

    @staticmethod
    def validate_special_option_choice(choice: str) -> Tuple[bool, Optional[str]]:
        """
        Validate special seat option choice (1, 2, 3, or 4) with enhanced validation.

        Args:
            choice: User's choice

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not choice:
            return False, "좌석 옵션을 선택해주세요. (1~4)"

        # Trim whitespace
        choice = choice.strip()

        if not choice:
            return False, "좌석 옵션을 선택해주세요. (1~4)"

        # Check if it's a digit
        if not choice.isdigit():
            return False, "숫자를 입력해주세요. (1, 2, 3, 또는 4)"

        # Validate choice
        if choice not in ["1", "2", "3", "4"]:
            return False, "1, 2, 3, 4 중 하나를 선택해주세요."

        return True, None

    @staticmethod
    def validate_passenger_count(count_str: str) -> Tuple[bool, Optional[str]]:
        """
        Validate passenger count with enhanced validation.

        Args:
            count_str: Passenger count as string

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not count_str:
            return False, "승객 수를 입력해주세요."

        # Trim whitespace
        count_str = count_str.strip()

        if not count_str:
            return False, "승객 수를 입력해주세요."

        # Check if it's a digit
        if not count_str.isdigit():
            return False, "승객 수는 숫자만 입력해주세요. (1~9)"

        try:
            count = int(count_str)
        except ValueError:
            return False, "유효한 숫자를 입력해주세요."

        # Validate range
        if count < 1:
            return False, "승객 수는 최소 1명 이상이어야 합니다."

        if count > 9:
            return False, "승객 수는 최대 9명까지 가능합니다."

        return True, None

    @staticmethod
    def validate_seat_strategy_choice(choice: str) -> Tuple[bool, Optional[str]]:
        """
        Validate seat strategy choice (1 or 2).

        Args:
            choice: User's choice

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not choice:
            return False, "좌석 배치 방식을 선택해주세요. (1: 연속 좌석, 2: 랜덤 배치)"

        # Trim whitespace
        choice = choice.strip()

        if not choice:
            return False, "좌석 배치 방식을 선택해주세요. (1: 연속 좌석, 2: 랜덤 배치)"

        # Check if it's a digit
        if not choice.isdigit():
            return False, "숫자를 입력해주세요. (1 또는 2)"

        # Validate choice
        if choice not in ["1", "2"]:
            return False, "1 또는 2를 입력해주세요. (1: 연속 좌석, 2: 랜덤 배치)"

        return True, None

    @staticmethod
    def validate_password(password: str) -> Tuple[bool, Optional[str]]:
        """
        Validate password input with basic security checks.

        Args:
            password: Password string

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not password:
            return False, "비밀번호를 입력해주세요."

        # Check minimum length
        if len(password) < 4:
            return False, "비밀번호가 너무 짧습니다."

        # Check maximum length (reasonable limit)
        if len(password) > 50:
            return False, "비밀번호가 너무 깁니다."

        # Check for suspicious patterns
        suspicious_patterns = ['<script', 'javascript:', 'onerror=', 'onclick=', 'SELECT', 'DROP', 'INSERT']
        if any(pattern.lower() in password.lower() for pattern in suspicious_patterns):
            return False, "유효하지 않은 문자가 포함되어 있습니다."

        return True, None

    # ==================== Card (automatic payment) ====================

    @staticmethod
    def _luhn_ok(digits: str) -> bool:
        total = 0
        for i, ch in enumerate(reversed(digits)):
            d = int(ch)
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return total % 10 == 0

    @staticmethod
    def validate_card_number(number: str) -> Tuple[bool, Optional[str]]:
        """Validate a credit card number (digits, optional hyphens/spaces, Luhn checksum)."""
        if not number or not number.strip():
            return False, "카드번호를 입력해주세요."
        digits = number.replace("-", "").replace(" ", "")
        if not digits.isdigit():
            return False, "카드번호는 숫자만 입력해주세요. (하이픈 생략 가능)"
        if len(digits) < 13 or len(digits) > 19:
            return False, "카드번호 자릿수가 올바르지 않습니다. (13~19자리)"
        if not InputValidator._luhn_ok(digits):
            return False, "카드번호가 올바르지 않습니다. 다시 확인해주세요."
        return True, None

    @staticmethod
    def normalize_card_number(number: str) -> str:
        return number.replace("-", "").replace(" ", "")

    @staticmethod
    def validate_card_expire(expire: str) -> Tuple[bool, Optional[str]]:
        """Validate card expiry. Accepts YYMM or MM/YY and checks it is not in the past."""
        if not expire or not expire.strip():
            return False, "유효기간을 입력해주세요."
        normalized = InputValidator.normalize_card_expire(expire)
        if normalized is None:
            return False, "유효기간 형식이 올바르지 않습니다. YYMM(예: 2812) 또는 MM/YY(예: 12/28)로 입력해주세요."
        yy, mm = int(normalized[:2]), int(normalized[2:])
        if mm < 1 or mm > 12:
            return False, f"유효기간의 월이 올바르지 않습니다. (입력: {mm}월)"
        today = datetime.today()
        if (2000 + yy, mm) < (today.year, today.month):
            return False, "이미 만료된 카드입니다."
        return True, None

    @staticmethod
    def normalize_card_expire(expire: str) -> Optional[str]:
        """Return expiry as YYMM. ``MM/YY`` and ``MM-YY`` are converted; None if unparsable."""
        value = expire.strip()
        if "/" in value or "-" in value:
            parts = value.replace("-", "/").split("/")
            if len(parts) != 2 or not all(p.isdigit() for p in parts):
                return None
            mm, yy = parts
            if len(mm) != 2 or len(yy) not in (2, 4):
                return None
            return f"{yy[-2:]}{mm}"
        if value.isdigit() and len(value) == 4:
            return value
        return None

    @staticmethod
    def validate_card_password(password: str) -> Tuple[bool, Optional[str]]:
        """Validate the first two digits of the card password."""
        if not password or not password.strip():
            return False, "카드 비밀번호 앞 2자리를 입력해주세요."
        value = password.strip()
        if not value.isdigit() or len(value) != 2:
            return False, "카드 비밀번호는 앞 2자리 숫자만 입력해주세요."
        return True, None

    @staticmethod
    def validate_card_verify_number(value: str) -> Tuple[bool, Optional[str]]:
        """Validate the owner verification number: YYMMDD birth date or 10-digit business number."""
        if not value or not value.strip():
            return False, "생년월일 6자리(개인) 또는 사업자등록번호 10자리(법인)를 입력해주세요."
        digits = value.strip().replace("-", "")
        if not digits.isdigit():
            return False, "숫자만 입력해주세요. (생년월일 YYMMDD 또는 사업자등록번호 10자리)"
        if len(digits) == 6:
            try:
                datetime.strptime(digits, "%y%m%d")
            except ValueError:
                return False, "생년월일이 올바르지 않습니다. (예: 900101)"
            return True, None
        if len(digits) == 10:
            return True, None
        return False, "생년월일은 6자리, 사업자등록번호는 10자리로 입력해주세요."
