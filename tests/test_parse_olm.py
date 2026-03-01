from pathlib import Path

from src.parse_olm import _extract_folder, _html_to_text, _parse_email_xml


def test_html_to_text_strips_tags_and_scripts():
    html = "<html><body><script>alert(1)</script><p>Hello<br>World</p></body></html>"
    text = _html_to_text(html)
    assert "Hello" in text
    assert "World" in text
    assert "alert" not in text


def test_extract_folder_from_path():
    path = "Accounts/user/com.microsoft.__Messages/Inbox/msg.xml"
    assert _extract_folder(path) == "Inbox"


def test_parse_email_xml_falls_back_to_no_subject():
    xml = b'''<?xml version="1.0"?>
<email xmlns="http://schemas.microsoft.com/outlook/mac/2011">
  <OPFMessageCopySenderAddress>a@example.com</OPFMessageCopySenderAddress>
  <OPFMessageCopySentTime>2023-01-01T00:00:00Z</OPFMessageCopySentTime>
</email>
'''

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.subject == "(no subject)"
    assert parsed.sender_email == "a@example.com"


def test_parse_email_xml_does_not_resolve_external_entities(tmp_path: Path):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("super-secret-token", encoding="utf-8")

    xml = f"""<?xml version="1.0"?>
<!DOCTYPE email [
<!ENTITY xxe SYSTEM "file://{secret_file}">
]>
<email xmlns="http://schemas.microsoft.com/outlook/mac/2011">
  <OPFMessageCopySubject>&xxe;</OPFMessageCopySubject>
  <OPFMessageCopySenderAddress>a@example.com</OPFMessageCopySenderAddress>
  <OPFMessageCopySentTime>2023-01-01T00:00:00Z</OPFMessageCopySentTime>
</email>
""".encode("utf-8")

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.subject != "super-secret-token"
