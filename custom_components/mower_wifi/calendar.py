from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, TASK_SCAN_INTERVAL
from .coordinator import MowerCoordinator

_LOGGER = logging.getLogger(__name__)

_WEEKDAY_FIELDS = (
    ("useOnMonday",    0),
    ("useOnTuesday",   1),
    ("useOnWednesday", 2),
    ("useOnThursday",  3),
    ("useOnFriday",    4),
    ("useOnSaturday",  5),
    ("useOnSunday",    6),
)


@dataclass
class _TaskData:
    start: int           # seconds after midnight (local time, mower has no TZ)
    duration: int        # seconds
    days: frozenset[int] # weekday ints: 0=Mon … 6=Sun


def _parse_task(raw: dict) -> _TaskData:
    days = frozenset(wd for field, wd in _WEEKDAY_FIELDS if raw.get(field, False))
    return _TaskData(start=int(raw["start"]), duration=int(raw["duration"]), days=days)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowerCalendar(coordinator, entry)])


class MowerCalendar(CoordinatorEntity[MowerCoordinator], CalendarEntity):
    """Calendar entity representing the mower's weekly mowing schedule."""

    _attr_has_entity_name = True
    _attr_name = "Mowing schedule"

    def __init__(self, coordinator: MowerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_calendar"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
        }
        self._tasks: list[_TaskData] = []
        self._tasks_last_fetched: datetime | None = None  # UTC

    @property
    def available(self) -> bool:
        return self.coordinator.connection_available

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_refresh_tasks()
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_scheduled_refresh,
                TASK_SCAN_INTERVAL,
            )
        )

    @callback
    def _async_scheduled_refresh(self, _now: datetime) -> None:
        self.hass.async_create_task(self._async_refresh_tasks())

    async def _async_refresh_tasks(self) -> None:
        mower = self.coordinator.mower
        try:
            count = await mower.send_command("GetNumberOfTasks")
            if count is None:
                _LOGGER.debug("GetNumberOfTasks returned None (mower unreachable)")
                return
            tasks: list[_TaskData] = []
            for task_id in range(int(count)):
                raw = await mower.send_command("GetTask", taskId=task_id)
                if raw is None:
                    _LOGGER.debug("GetTask(%d) returned None, skipping", task_id)
                    continue
                tasks.append(_parse_task(raw))
            self._tasks = tasks
            self._tasks_last_fetched = dt_util.utcnow()
            _LOGGER.debug("Fetched %d mower tasks", len(self._tasks))
        except Exception as err:
            _LOGGER.debug("Error fetching mower tasks: %s", err)
        self.async_write_ha_state()

    def _is_task_cache_stale(self) -> bool:
        if self._tasks_last_fetched is None:
            return True
        return (dt_util.utcnow() - self._tasks_last_fetched) > TASK_SCAN_INTERVAL

    @property
    def event(self) -> CalendarEvent | None:
        return self._get_next_event(dt_util.now())

    def _get_next_event(self, now: datetime) -> CalendarEvent | None:
        tz = dt_util.get_default_time_zone()
        now_local = now.astimezone(tz)
        window_end = now_local + timedelta(days=8)
        best: CalendarEvent | None = None
        for task in self._tasks:
            if not task.days or task.duration <= 0:
                continue
            event = self._next_occurrence(task, now_local, window_end)
            if event is not None and (best is None or event.start < best.start):
                best = event
        return best

    def _next_occurrence(
        self,
        task: _TaskData,
        from_dt: datetime,
        until_dt: datetime,
    ) -> CalendarEvent | None:
        tz = from_dt.tzinfo
        current_date = from_dt.date()
        for _ in range(8):
            if current_date.weekday() in task.days:
                event_start = datetime(
                    current_date.year, current_date.month, current_date.day, tzinfo=tz
                ) + timedelta(seconds=task.start)
                event_end = event_start + timedelta(seconds=task.duration)
                if event_end > from_dt and event_start < until_dt:
                    return CalendarEvent(summary="Mowing", start=event_start, end=event_end)
            current_date += timedelta(days=1)
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        if self._is_task_cache_stale():
            await self._async_refresh_tasks()

        tz = dt_util.get_default_time_zone()
        start_local = start_date.astimezone(tz)
        end_local = end_date.astimezone(tz)

        events: list[CalendarEvent] = []
        for task in self._tasks:
            if not task.days or task.duration <= 0:
                continue
            events.extend(self._expand_task(task, start_local, end_local))

        events.sort(key=lambda e: e.start)
        return events

    def _expand_task(
        self,
        task: _TaskData,
        start_local: datetime,
        end_local: datetime,
    ) -> list[CalendarEvent]:
        tz = start_local.tzinfo
        results: list[CalendarEvent] = []
        current_date = start_local.date()
        end_date = end_local.date()
        while current_date <= end_date:
            if current_date.weekday() in task.days:
                event_start = datetime(
                    current_date.year, current_date.month, current_date.day, tzinfo=tz
                ) + timedelta(seconds=task.start)
                event_end = event_start + timedelta(seconds=task.duration)
                if event_start < end_local and event_end > start_local:
                    results.append(CalendarEvent(summary="Mowing", start=event_start, end=event_end))
            current_date += timedelta(days=1)
        return results

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
