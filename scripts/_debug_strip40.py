"""Télécharge STRIP-40 et ACE-7 via item ID /content."""
import pathlib
import sys
import msal
import requests

CLIENT_ID = "e5e5bc3b-aeb0-4fbb-8cd8-76a90a8fcf11"
TENANT_ID = "21aa992f-60cb-4048-b8b9-64e1fb98e11f"
SCOPES = ["https://graph.microsoft.com/Files.Read.All", "https://graph.microsoft.com/Sites.Read.All"]
GRAPH = "https://graph.microsoft.com/v1.0"
SITE_HOSTNAME = "nxtfoodfr.sharepoint.com"
DEST = pathlib.Path("data/repertoire_rd_2025-2026/lien_essai/lien_essai_brut")

app = msal.PublicClientApplication(CLIENT_ID, authority=f"https://login.microsoftonline.com/{TENANT_ID}")
flow = app.initiate_device_flow(scopes=SCOPES)
print(flow["message"], flush=True)
result = app.acquire_token_by_device_flow(flow)
if "access_token" not in result:
    print("ERREUR:", result.get("error_description")); sys.exit(1)

s = requests.Session()
s.headers["Authorization"] = f"Bearer {result['access_token']}"

site = s.get(f"{GRAPH}/sites/{SITE_HOSTNAME}:/sites/RDIndustrieCollaboration").json()
site_id = site["id"]
drives = s.get(f"{GRAPH}/sites/{site_id}/drives", params={"$select": "id"}).json()
drive_id = drives["value"][0]["id"]

# Item IDs trouvés à l'étape précédente
ITEMS = {
    "STRIP-40": ("01MKBX6O56VN3EXTFM6RC2XDZIQDZ3XJZN", "Fiche essai industriel R&D YD 29.09.25.xlsx",
                 "https://nxtfoodfr.sharepoint.com/sites/RDIndustrieCollaboration/_layouts/15/Doc.aspx?sourcedoc=%7B4B76ABBE-ACCC-45F4-AB8F-2880F3BBA72D%7D&file=Fiche%20essai%20industriel%20R%26D%20YD%2029.09.25.xlsx&action=default&mobileredirect=true&DefaultItemOpen=1"),
    "ACE-7":    ("01MKBX6O5ECZ4WSUKDKFAI3P6ACURPI2AZ", "Fiche essai industriel R&D YD 01.10.25.xlsx",
                 "https://nxtfoodfr.sharepoint.com/sites/RDIndustrieCollaboration/_layouts/15/Doc.aspx?sourcedoc=%7B697916A4-4351-4051-8DBF-C01522F46819%7D&file=Fiche%20essai%20industriel%20R%26D%20YD%2001.10.25.xlsx&action=default&mobileredirect=true&DefaultItemOpen=1"),
}

for label, (item_id, filename, web_url) in ITEMS.items():
    print(f"\n=== {label} ===")
    dest = DEST / f"{label}.xlsx"

    resp = s.get(f"{GRAPH}/drives/{drive_id}/items/{item_id}/content", allow_redirects=True)
    if resp.ok:
        dest.write_bytes(resp.content)
        print(f"  ✓ {dest} ({len(resp.content)//1024} KB)")
        print(f"  WEBURL: label='{label}' url={web_url}")
    else:
        print(f"  ✗ HTTP {resp.status_code}: {resp.text[:200]}")
