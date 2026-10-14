"""SDK 2 can replace Connection per exchange; ownership follows the trusted duplex endpoint."""

from types import SimpleNamespace
import json

from fastmcp import Client, Context, FastMCP

from windows_mcp.engine.sessions import EngineRuntime


class Endpoint:
    pass


def context(endpoint):
    connection = SimpleNamespace(session_id=None, outbound=SimpleNamespace(_outbound=endpoint))
    return SimpleNamespace(session=SimpleNamespace(_connection=connection), session_id="ephemeral")


def test_fresh_connections_keep_the_same_client_owner():
    runtime = EngineRuntime()
    endpoint = Endpoint()
    assert runtime.owner(context(endpoint)) == runtime.owner(context(endpoint))


def test_independent_transport_endpoints_never_share_owners():
    runtime = EngineRuntime()
    a, b = Endpoint(), Endpoint()
    assert runtime.owner(context(a)) != runtime.owner(context(b))


def test_sdk_one_uses_its_persistent_session_identity():
    runtime = EngineRuntime()
    assert runtime.owner(SimpleNamespace(session=object(), session_id="persistent")) == "persistent"


def test_no_backchannel_placeholder_does_not_merge_clients():
    class _NoChannelOutbound:
        pass

    runtime = EngineRuntime()
    shared_placeholder = _NoChannelOutbound()
    a, b = context(shared_placeholder), context(shared_placeholder)
    a.session_id, b.session_id = "A", "B"
    assert runtime.owner(a) != runtime.owner(b)


async def test_real_protocol_maintains_identity_and_separates_clients():
    runtime = EngineRuntime()
    server = FastMCP("identity-regression")

    @server.tool(name="Identity")
    async def identity(ctx: Context) -> dict:
        return {"session_id": runtime.session(runtime.owner(ctx)).session_id}

    async def read(client):
        result = await client.call_tool("Identity", {})
        return json.loads(result.content[0].text)["session_id"]

    async with Client(server) as a:
        first = await read(a)
        assert await read(a) == first
        async with Client(server) as b:
            assert await read(b) != first
            assert await read(a) == first
