import os
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, status
from pydantic import BaseModel, Field

from ...devices import CodeSet, DeviceStorage
from ...devices.catalogs.importer import (
    CatalogValidationError,
    parse_custom_catalog_json,
    parse_smartir_json,
)
from ...devices.catalogs.seed_data import get_seed_code_sets

router = APIRouter(prefix="/api/catalog", tags=["Catalog"])


class ImportCatalogRequest(BaseModel):
    catalog_json: Dict[str, Any] = Field(..., description="Dữ liệu JSON của catalog SmartIR hoặc custom CodeSet")
    source_name: str = Field(default="User Import", description="Nguồn catalog")


@router.get("/brands")
def list_brands(category: Optional[str] = None):
    storage = DeviceStorage()
    include_quarantined = os.environ.get("SMART_HUB_MOCK_HARDWARE") == "1"
    brands = storage.list_brands(category=category, include_quarantined=include_quarantined)
    return brands


@router.get("/code-sets")
def list_code_sets(category: Optional[str] = None, brand: Optional[str] = None):
    storage = DeviceStorage()
    include_quarantined = os.environ.get("SMART_HUB_MOCK_HARDWARE") == "1"
    code_sets = storage.list_code_sets(category=category, brand=brand, include_quarantined=include_quarantined)
    return [cs.to_dict() for cs in code_sets]


@router.get("/code-sets/{code_set_id}")
def get_code_set(code_set_id: str):
    storage = DeviceStorage()
    cs = storage.get_code_set(code_set_id)
    if not cs:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy bộ mã '{code_set_id}'")
    if cs.codes and os.environ.get("SMART_HUB_MOCK_HARDWARE") != "1":
        # Check if quarantined
        if getattr(cs, "is_quarantined", False) or cs.id.endswith("_seed"):
            raise HTTPException(status_code=404, detail=f"Bộ mã '{code_set_id}' thuộc mock seed đã bị cách ly.")
    return cs.to_dict()


@router.post("/imports")
def import_catalog(req: ImportCatalogRequest):
    storage = DeviceStorage()
    try:
        data = req.catalog_json
        if "supportedController" in data or ("commands" in data and "manufacturer" in data):
            codeset = parse_smartir_json(data, source_url=req.source_name)
        else:
            codeset = parse_custom_catalog_json(data, source_name=req.source_name)

        storage.save_code_set(codeset)
        return {
            "status": "ok",
            "message": f"Đã nhập thành công bộ mã '{codeset.brand}' với {len(codeset.codes)} nút/preset.",
            "code_set": codeset.to_dict(),
        }
    except CatalogValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Lỗi nhập catalog: {exc}")


@router.post("/seed")
def seed_catalog(req: Optional[Dict[str, Any]] = None):
    storage = DeviceStorage()
    for cs in get_seed_code_sets():
        storage.save_code_set(cs)
    return {"status": "ok", "message": "Catalog đã được nạp dữ liệu mẫu."}


@router.post("/quarantine")
def quarantine_seeds():
    storage = DeviceStorage()
    res = storage.quarantine_mock_seeds(backup=True)
    return {"status": "ok", "quarantined": res}
