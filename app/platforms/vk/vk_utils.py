from urllib.parse import urlparse

import httpx

from app.services.config import settings

VK_API = "https://api.vk.com/method"

async def _call_vk_method(client: httpx.AsyncClient, method: str, data: dict):
    response = await client.post(
        f"{VK_API}/{method}",
        data={
            "access_token": settings.VK_GROUP_TOKEN,
            "v": "5.131",
            **data,
        },
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"VK API {method} error: {payload['error']}")
    return payload.get("response")


def _guess_image_filename(image_url: str, content_type: str | None) -> str:
    parsed = urlparse(image_url)
    name = parsed.path.rsplit("/", 1)[-1] if parsed.path else ""
    if name:
        return name
    if content_type == "image/png":
        return "broadcast.png"
    if content_type == "image/webp":
        return "broadcast.webp"
    if content_type == "image/gif":
        return "broadcast.gif"
    return "broadcast.jpg"


def _build_vk_photo_attachment(photo_payload: list[dict] | dict) -> str:
    photo = photo_payload[0] if isinstance(photo_payload, list) else photo_payload
    owner_id = photo["owner_id"]
    photo_id = photo["id"]
    access_key = photo.get("access_key")
    attachment = f"photo{owner_id}_{photo_id}"
    if access_key:
        attachment = f"{attachment}_{access_key}"
    return attachment


async def upload_message_photo(image_url: str) -> str:
    async with httpx.AsyncClient(timeout=20.0) as client:
        upload_server = await _call_vk_method(client, "photos.getMessagesUploadServer", {})
        upload_url = upload_server.get("upload_url")
        if not upload_url:
            raise RuntimeError("VK upload server did not return upload_url")

        image_response = await client.get(image_url)
        image_response.raise_for_status()
        content_type = image_response.headers.get("content-type", "").split(";", 1)[0].strip().lower() or None
        if content_type and not content_type.startswith("image/"):
            raise RuntimeError(f"Unsupported image content type: {content_type}")

        upload_response = await client.post(
            upload_url,
            files={
                "photo": (
                    _guess_image_filename(image_url, content_type),
                    image_response.content,
                    content_type or "image/jpeg",
                )
            },
        )
        upload_response.raise_for_status()
        upload_payload = upload_response.json()
        saved = await _call_vk_method(
            client,
            "photos.saveMessagesPhoto",
            {
                "photo": upload_payload["photo"],
                "server": upload_payload["server"],
                "hash": upload_payload["hash"],
            },
        )
        return _build_vk_photo_attachment(saved)


async def send_message(
    peer_id: int,
    text: str,
    keyboard: str | None = None,
    attachment: str | None = None,
):
    params = {
        'peer_id': peer_id,
        'message': text,
        'random_id': 0,
    }
    if keyboard:
        params['keyboard'] = keyboard
    if attachment:
        params['attachment'] = attachment
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await _call_vk_method(client, "messages.send", params)
        return {"response": response}

async def edit_message(peer_id: int, message_id: int, text: str):
    url = f"{VK_API}/messages.edit"
    params = {
        'access_token': settings.VK_GROUP_TOKEN,
        'v': '5.131',
        'peer_id': peer_id,
        'message_id': message_id,
        'message': text
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, data=params)
        r.raise_for_status()
        return r.json()
