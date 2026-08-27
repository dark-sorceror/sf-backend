import base64

import pytest
from sqlalchemy import text

from app.database import engine

BASE = "/api/v1/contacts"

HOME = {
    "type": "Home",
    "address": "1 Market St, Suite 400",
    "city": "San Francisco",
    "state": "CA",
    "postal_code": "94105",
    "country": "USA",
}
WORK = {"type": "Work", "address": "500 Terry Francois Blvd", "city": "San Francisco", "postal_code": "94158"}
OTHER = {"type": "Other", "address": "PO Box 12", "city": "Reno", "state": "NV"}


def _types(body: dict) -> list[str]:
    return [address["type"] for address in body["addresses"]]


def _address_ids(body: dict) -> list[int]:
    return [address["id"] for address in body["addresses"]]


def _stored_address_count() -> int:
    """Orphaned child rows are invisible over HTTP, so read the table directly."""
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM addresses")).scalar_one()

# The smallest real image of each accepted type, encoded the way a browser's
# FileReader would hand it to the frontend.
PNG_PHOTO = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
JPEG_PHOTO = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2Q=="
WEBP_PHOTO = "data:image/webp;base64,UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA"


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "sqlite"


def test_create_contact(client, payload):
    response = client.post(BASE, json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["email"] == "ada@example.com"
    assert body["full_name"] == "Ada Lovelace"
    assert body["created_at"] and body["updated_at"]


def test_create_requires_valid_email(client, payload):
    response = client.post(BASE, json={**payload, "email": "not-an-email"})
    assert response.status_code == 422


def test_create_requires_names(client, payload):
    response = client.post(BASE, json={**payload, "first_name": ""})
    assert response.status_code == 422


def test_duplicate_email_conflicts(client, payload):
    assert client.post(BASE, json=payload).status_code == 201
    response = client.post(BASE, json={**payload, "email": "ADA@example.com"})
    assert response.status_code == 409


def test_get_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.get(f"{BASE}/{contact_id}")
    assert response.status_code == 200
    assert response.json()["id"] == contact_id


def test_get_missing_contact_returns_404(client):
    assert client.get(f"{BASE}/9999").status_code == 404


def test_list_pagination_and_total(client, payload):
    for index in range(5):
        client.post(BASE, json={**payload, "email": f"user{index}@example.com"})

    response = client.get(BASE, params={"limit": 2, "offset": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2 and body["offset"] == 2


def test_list_search(client, payload):
    client.post(BASE, json=payload)
    client.post(
        BASE,
        json={**payload, "first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com", "company": "US Navy"},
    )

    hits = client.get(BASE, params={"search": "hopper"}).json()
    assert hits["total"] == 1
    assert hits["items"][0]["last_name"] == "Hopper"

    by_company = client.get(BASE, params={"search": "navy"}).json()
    assert by_company["total"] == 1

    misses = client.get(BASE, params={"search": "nobody"}).json()
    assert misses["total"] == 0


def test_list_sorting(client, payload):
    client.post(BASE, json={**payload, "last_name": "Zhang", "email": "z@example.com"})
    client.post(BASE, json={**payload, "last_name": "Adams", "email": "a@example.com"})

    names = [
        item["last_name"]
        for item in client.get(BASE, params={"sort_by": "last_name", "order": "asc"}).json()["items"]
    ]
    assert names == ["Adams", "Zhang"]


def test_list_rejects_bad_sort_field(client):
    assert client.get(BASE, params={"sort_by": "; DROP TABLE contacts"}).status_code == 422


def test_patch_updates_only_sent_fields(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"phone": "+1-000-000-0000"})
    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == "+1-000-000-0000"
    assert body["first_name"] == "Ada"
    assert body["company"] == "Analytical Engines"


def test_patch_duplicate_email_conflicts(client, payload):
    first = client.post(BASE, json=payload).json()["id"]
    client.post(BASE, json={**payload, "email": "grace@example.com"})
    response = client.patch(f"{BASE}/{first}", json={"email": "grace@example.com"})
    assert response.status_code == 409


def test_patch_same_email_is_allowed(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"email": payload["email"]})
    assert response.status_code == 200


def test_put_replaces_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.put(
        f"{BASE}/{contact_id}",
        json={"first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Grace Hopper"
    assert body["company"] is None  # omitted fields are cleared by PUT


def test_put_missing_contact_returns_404(client):
    response = client.put(
        f"{BASE}/9999",
        json={"first_name": "A", "last_name": "B", "email": "ab@example.com"},
    )
    assert response.status_code == 404


def test_delete_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    assert client.delete(f"{BASE}/{contact_id}").status_code == 204
    assert client.get(f"{BASE}/{contact_id}").status_code == 404
    assert client.delete(f"{BASE}/{contact_id}").status_code == 404


def test_root_lists_entrypoints(client):
    body = client.get("/").json()
    assert body["contacts"] == BASE


def test_create_without_photo_returns_null(client, payload):
    response = client.post(BASE, json=payload)
    assert response.status_code == 201
    assert response.json()["photo"] is None


@pytest.mark.parametrize("photo", [PNG_PHOTO, JPEG_PHOTO, WEBP_PHOTO])
def test_create_with_photo_round_trips(client, payload, photo):
    response = client.post(BASE, json={**payload, "photo": photo})
    assert response.status_code == 201
    assert response.json()["photo"] == photo

    contact_id = response.json()["id"]
    assert client.get(f"{BASE}/{contact_id}").json()["photo"] == photo


@pytest.mark.parametrize(
    "photo",
    [
        "data:image/gif;base64,R0lGODlhAQABAAAAACw=",
        "data:image/svg+xml;base64,PHN2Zy8+",
        "data:application/pdf;base64,JVBERi0=",
        "https://example.com/ada.png",
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ",
        "data:image/png;base64,not valid base64!!",
    ],
)
def test_create_rejects_unsupported_photo(client, payload, photo):
    assert client.post(BASE, json={**payload, "photo": photo}).status_code == 422


def test_create_rejects_photo_over_the_size_limit(client, payload):
    oversized = "data:image/png;base64," + "A" * (700 * 1024)
    assert client.post(BASE, json={**payload, "photo": oversized}).status_code == 422


@pytest.mark.parametrize("encoded", ["a", "AB", "abc", "AAAAA"])
def test_create_rejects_malformed_base64_photo(client, payload, encoded):
    # Right alphabet, impossible length: these are not decodable base64.
    response = client.post(BASE, json={**payload, "photo": f"data:image/png;base64,{encoded}"})
    assert response.status_code == 422


def test_create_rejects_photo_just_over_the_size_limit(client, payload):
    # 31 bytes over the cap -- small enough that the character-length pre-filter
    # lets it through, so this pins the decoded-size check specifically.
    encoded = base64.b64encode(b"\x00" * (500 * 1024 + 31)).decode()
    response = client.post(BASE, json={**payload, "photo": f"data:image/png;base64,{encoded}"})
    assert response.status_code == 422


def test_create_accepts_photo_at_the_size_limit(client, payload):
    # 500 KB of source bytes is exactly what the frontend lets through.
    encoded = base64.b64encode(b"\x00" * (500 * 1024)).decode()
    response = client.post(BASE, json={**payload, "photo": f"data:image/png;base64,{encoded}"})
    assert response.status_code == 201


def test_patch_sets_photo_without_touching_other_fields(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"photo": PNG_PHOTO})
    assert response.status_code == 200
    body = response.json()
    assert body["photo"] == PNG_PHOTO
    assert body["first_name"] == "Ada"
    assert body["company"] == "Analytical Engines"


def test_patch_null_removes_photo(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PNG_PHOTO}).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"photo": None})
    assert response.status_code == 200
    assert response.json()["photo"] is None
    assert client.get(f"{BASE}/{contact_id}").json()["photo"] is None


def test_patch_rejects_unsupported_photo(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"photo": "data:image/gif;base64,R0lGODlhAQABAAAAACw="})
    assert response.status_code == 422


def test_put_resending_photo_preserves_it(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PNG_PHOTO}).json()["id"]
    response = client.put(
        f"{BASE}/{contact_id}",
        json={**payload, "photo": PNG_PHOTO, "job_title": "Chief Engineer"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["photo"] == PNG_PHOTO
    assert body["job_title"] == "Chief Engineer"


def test_put_omitting_photo_clears_it(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PNG_PHOTO}).json()["id"]
    response = client.put(f"{BASE}/{contact_id}", json=payload)  # `payload` carries no photo
    assert response.status_code == 200
    assert response.json()["photo"] is None  # PUT is a full replacement, photo included


def test_create_contact_with_no_addresses(client, payload):
    response = client.post(BASE, json=payload)
    assert response.status_code == 201
    assert response.json()["addresses"] == []


def test_create_contact_with_one_home_address(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [HOME]})
    assert response.status_code == 201
    addresses = response.json()["addresses"]
    assert len(addresses) == 1
    assert addresses[0]["type"] == "Home"
    assert addresses[0]["city"] == "San Francisco"
    assert addresses[0]["id"] > 0


def test_create_contact_with_multiple_addresses(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [HOME, WORK, OTHER]})
    assert response.status_code == 201
    assert _types(response.json()) == ["Home", "Work", "Other"]
    assert len(set(_address_ids(response.json()))) == 3


def test_get_returns_all_addresses(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": [HOME, WORK]}).json()["id"]
    body = client.get(f"{BASE}/{contact_id}").json()
    assert _types(body) == ["Home", "Work"]
    assert body["addresses"][1]["postal_code"] == "94158"
    assert body["addresses"][1]["state"] is None


def test_list_includes_nested_addresses(client, payload):
    client.post(BASE, json={**payload, "addresses": [HOME]})
    items = client.get(BASE).json()["items"]
    assert _types(items[0]) == ["Home"]


@pytest.mark.parametrize("address_type", ["Home", "Work", "Other"])
def test_every_address_type_is_accepted(client, payload, address_type):
    response = client.post(BASE, json={**payload, "addresses": [{**HOME, "type": address_type}]})
    assert response.status_code == 201
    assert response.json()["addresses"][0]["type"] == address_type


@pytest.mark.parametrize("address_type", ["home", "HOME", "Business", "", None, 1])
def test_invalid_address_type_is_rejected(client, payload, address_type):
    response = client.post(BASE, json={**payload, "addresses": [{**HOME, "type": address_type}]})
    assert response.status_code == 422


def test_address_type_is_required(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [{"city": "San Francisco"}]})
    assert response.status_code == 422


def test_put_replaces_the_whole_address_collection(client, payload):
    created = client.post(BASE, json={**payload, "addresses": [HOME, WORK]}).json()
    contact_id = created["id"]

    response = client.put(f"{BASE}/{contact_id}", json={**payload, "addresses": [OTHER]})
    assert response.status_code == 200
    body = response.json()
    assert _types(body) == ["Other"]
    # New rows, not the old ones edited in place.
    assert not set(_address_ids(body)) & set(_address_ids(created))
    assert _types(client.get(f"{BASE}/{contact_id}").json()) == ["Other"]
    assert _stored_address_count() == 1  # the two originals are gone, not orphaned


def test_put_dropping_an_address_removes_it(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": [HOME, WORK]}).json()["id"]
    response = client.put(f"{BASE}/{contact_id}", json={**payload, "addresses": [HOME]})
    assert response.status_code == 200
    assert _types(response.json()) == ["Home"]
    assert _stored_address_count() == 1


def test_put_omitting_addresses_clears_them(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": [HOME, WORK]}).json()["id"]
    response = client.put(f"{BASE}/{contact_id}", json=payload)  # `payload` carries no addresses
    assert response.status_code == 200
    assert response.json()["addresses"] == []  # PUT is a full replacement
    assert _stored_address_count() == 0


def test_patch_omitting_addresses_preserves_them(client, payload):
    created = client.post(BASE, json={**payload, "addresses": [HOME, WORK]}).json()
    response = client.patch(f"{BASE}/{created['id']}", json={"job_title": "Chief Engineer"})
    assert response.status_code == 200
    body = response.json()
    assert body["job_title"] == "Chief Engineer"
    assert _address_ids(body) == _address_ids(created)  # same rows, untouched


def test_patch_with_addresses_replaces_the_collection(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": [HOME, WORK]}).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"addresses": [OTHER]})
    assert response.status_code == 200
    assert _types(response.json()) == ["Other"]
    assert _stored_address_count() == 1


def test_patch_with_empty_addresses_clears_them(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": [HOME, WORK]}).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"addresses": []})
    assert response.status_code == 200
    assert response.json()["addresses"] == []
    assert client.get(f"{BASE}/{contact_id}").json()["addresses"] == []
    assert _stored_address_count() == 0


def test_deleting_a_contact_removes_its_addresses(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": [HOME, WORK]}).json()["id"]
    assert _stored_address_count() == 2
    assert client.delete(f"{BASE}/{contact_id}").status_code == 204
    assert _stored_address_count() == 0  # no orphan child rows


def test_photo_and_addresses_coexist(client, payload):
    created = client.post(BASE, json={**payload, "photo": PNG_PHOTO, "addresses": [HOME, WORK]}).json()
    contact_id = created["id"]
    assert created["photo"] == PNG_PHOTO
    assert _types(created) == ["Home", "Work"]

    # Replacing addresses must not disturb the photo...
    patched = client.patch(f"{BASE}/{contact_id}", json={"addresses": [OTHER]}).json()
    assert patched["photo"] == PNG_PHOTO
    assert _types(patched) == ["Other"]

    # ...nor the reverse.
    patched = client.patch(f"{BASE}/{contact_id}", json={"photo": None}).json()
    assert patched["photo"] is None
    assert _types(patched) == ["Other"]
