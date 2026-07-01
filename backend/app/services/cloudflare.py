"""Cloudflare DNS API helper — create/update A record for a domain."""
import httpx


CF_BASE = "https://api.cloudflare.com/client/v4"


async def get_zone_id(api_token: str, domain: str) -> str:
    """Resolve zone id from the root domain extracted from domain."""
    root = ".".join(domain.split(".")[-2:])
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{CF_BASE}/zones",
            headers={"Authorization": f"Bearer {api_token}"},
            params={"name": root},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if not data["result"]:
            raise ValueError(f"No Cloudflare zone found for {root}")
        return data["result"][0]["id"]


async def upsert_a_record(api_token: str, domain: str, ip: str) -> None:
    """Create or update A record pointing domain -> ip."""
    zone_id = await get_zone_id(api_token, domain)
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    payload = {"type": "A", "name": domain, "content": ip, "ttl": 1, "proxied": False}

    async with httpx.AsyncClient() as client:
        # Check if record already exists
        r = await client.get(
            f"{CF_BASE}/zones/{zone_id}/dns_records",
            headers=headers,
            params={"type": "A", "name": domain},
            timeout=15,
        )
        r.raise_for_status()
        records = r.json()["result"]

        if records:
            record_id = records[0]["id"]
            r = await client.put(
                f"{CF_BASE}/zones/{zone_id}/dns_records/{record_id}",
                headers=headers,
                json=payload,
                timeout=15,
            )
        else:
            r = await client.post(
                f"{CF_BASE}/zones/{zone_id}/dns_records",
                headers=headers,
                json=payload,
                timeout=15,
            )
        r.raise_for_status()
