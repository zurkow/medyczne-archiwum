import asyncio

from medical_archive.backup.maintenance import MaintenanceState


def test_maintenance_drain_waits_for_other_in_flight_requests():
    async def scenario() -> tuple[bool, bool, int, bool]:
        state = MaintenanceState()
        state.in_flight = 2
        state.enter()

        async def finish_other_request() -> None:
            await asyncio.sleep(0.2)
            state.in_flight -= 1

        finisher = asyncio.create_task(finish_other_request())
        drained = await state.wait_until_drained(timeout_seconds=5)
        remaining = state.in_flight
        await finisher

        state.in_flight = 3
        timed_out = await state.wait_until_drained(timeout_seconds=0.2)
        still_active = state.active
        state.exit()
        return drained, timed_out, remaining, still_active and not state.active

    drained, timed_out, remaining, toggled = asyncio.run(scenario())

    assert drained is True
    assert remaining == 1
    assert timed_out is False
    assert toggled is True
