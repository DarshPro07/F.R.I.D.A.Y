import asyncio
import pytest
from tests.conftest import fresh_event_loop


@pytest.fixture
def probe():
    loop = asyncio.get_event_loop()
    print('FIXTURE_LOOP_ID:', id(loop))


@pytest.mark.asyncio
async def test_probe(probe):
    print('TEST_LOOP_ID:', id(asyncio.get_event_loop()))
    print('RUNNING_LOOP_ID:', id(asyncio.get_running_loop()))