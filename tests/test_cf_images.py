import httpx

from aviary.common import cf_images


def test_upload_image_sends_multipart_form_data(monkeypatch):
    """Cloudflare answers 415 to a urlencoded body (httpx's default for
    `data`), so the id and the source URL must go as multipart fields."""
    monkeypatch.setenv("CF_IMAGES_ACCOUNT_ID", "account")
    monkeypatch.setenv("CF_IMAGES_API_KEY", "key")
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"success": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cf_images, "_is_removed_imgur", lambda *_: False)

    result = cf_images.upload_image(
        client, "artisan/m/s/c", "https://example.com/a.jpg"
    )

    assert result.ok
    (request,) = sent
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.read().decode()
    assert 'name="id"' in body and "artisan/m/s/c" in body
    assert 'name="url"' in body and "https://example.com/a.jpg" in body
