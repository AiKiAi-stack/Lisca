"""UI language assets are available to viewers; management stays local."""
import asyncio
from pathlib import Path

import httpx

from lisca.web import app as webui  # noqa: E402


def test_language_script_is_served_without_exposing_management():
    async def check():
        for address in ("127.0.0.1", "192.168.1.20"):
            transport = httpx.ASGITransport(app=webui.app, client=(address, 12345))
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.get("/i18n.js")
                assert response.status_code == 200
                assert "no-store" in response.headers["cache-control"]
                assert "createI18n" in response.text
                assert (await client.get("/recovery_controller.js")).status_code == 200
                library = await client.get("/source_library.js")
                assert library.status_code == 200
                assert "RecentChannels" in library.text
                components = await client.get("/ui-components.css")
                assert components.status_code == 200
                assert "daisyUI 5.7.50" in components.text
                icons = await client.get("/icons.svg")
                assert icons.status_code == 200
                assert icons.headers["content-type"].startswith("image/svg+xml")
                favicon = await client.get("/favicon.svg")
                assert favicon.status_code == 200
                assert favicon.headers["content-type"].startswith("image/svg+xml")
                configuration = await client.get("/api/cloud")
                assert configuration.status_code == (200 if address == "127.0.0.1" else 403)
                page = await client.get("/?lang=en")
                assert page.status_code == (200 if address == "127.0.0.1" else 403)
                if page.status_code == 200:
                    assert 'src="/i18n.js"' in page.text
                    assert 'rel="icon" href="/favicon.svg"' in page.text
                    assert 'id="uiLanguage"' in page.text
                    assert "<h1>Lisca</h1>" in page.text
    asyncio.run(check())
