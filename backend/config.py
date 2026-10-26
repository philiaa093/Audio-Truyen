"""Đường dẫn dùng chung, độc lập với thư mục làm việc của tiến trình."""

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
REPORT_PATH = ROOT_DIR / "docs" / "deep-research-report.md"
STORAGE_DIR = ROOT_DIR / "storage"
PROJECTS_DIR = STORAGE_DIR / "projects"
