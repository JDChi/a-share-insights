"""Opt-in real-source API smoke check for the three documented companies."""
from fastapi.testclient import TestClient
from ashare_insights.main import create_app


def main():
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        for code in ("600519", "000333", "430418"):
            search = client.get("/api/v1/companies", params={"q": code})
            search.raise_for_status()
            company = next(item for item in search.json()["items"] if item["code"] == code)
            response = client.get("/api/v1/reports", params={"company_id": company["id"], "years": "2024", "refresh": True})
            response.raise_for_status()
            reports = response.json()["items"]
            assert reports, f"{code} lacks a 2024 annual report"
            print(code, company["name"], [(item["announcement_id"], item["title"]) for item in reports])
            if code == "430418":
                continue
            report = reports[0]
            task = client.post(f'/api/v1/reports/{report["id"]}/download')
            task.raise_for_status()
            app.state.downloads.queue.join()
            status = client.get(f'/api/v1/downloads/{task.json()["id"]}').json()
            assert status["status"] == "completed", status
            pdf = client.get(f'/api/v1/reports/{report["id"]}/file')
            pdf.raise_for_status()
            assert pdf.content.startswith(b"%PDF-")
            print("  PDF", len(pdf.content), "bytes")


if __name__ == "__main__":
    main()
