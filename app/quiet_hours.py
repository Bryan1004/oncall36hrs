from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, model_validator


class QuietHours(BaseModel):
    enabled: bool = False
    start: str = Field(default='18:00', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    end: str = Field(default='09:00', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    days: list[int] = Field(default_factory=lambda: list(range(7)), max_length=7)
    timezone: str = 'Asia/Kuala_Lumpur'

    @model_validator(mode='after')
    def validate_schedule(self):
        if any(day not in range(7) for day in self.days):
            raise ValueError('星期无效')
        if self.enabled and (not self.days or self.start == self.end):
            raise ValueError('请选择星期，并设置不同的开始和结束时间')
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError):
            raise ValueError('时区无效') from None
        return self


def quiet_active(store, now=None):
    schedule = QuietHours(**(store.get('quiet_hours') or {}))
    if not schedule.enabled:
        return False
    local = datetime.fromtimestamp(now, ZoneInfo(schedule.timezone)) if now is not None else datetime.now(ZoneInfo(schedule.timezone))
    minute = local.strftime('%H:%M')
    if schedule.start < schedule.end:
        return local.weekday() in schedule.days and schedule.start <= minute < schedule.end
    # Selected weekday is the day the overnight quiet period starts.
    return (local.weekday() in schedule.days and minute >= schedule.start) or ((local - timedelta(days=1)).weekday() in schedule.days and minute < schedule.end)
