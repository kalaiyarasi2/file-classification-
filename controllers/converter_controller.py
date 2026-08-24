from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from services.converter_service import ConverterService
from database.db import get_db

router = APIRouter()
service = ConverterService()


def _respond_with_file(result: dict) -> FileResponse:
    return FileResponse(
        path=result["output_path"],
        filename=result["download_name"],
        media_type=result["content_type"],
    )


@router.post("/")
async def convert_file(
    source_format: str = Form(...),
    target_format: str = Form(...),
    user_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Common endpoint: routes to the correct converter based on source/target format."""
    try:
        result = await service.convert_file(
            source_format, target_format, file, db, user_id
        )
        return _respond_with_file(result)
    except ValueError as ex:
        # Unsupported conversion type
        raise HTTPException(status_code=400, detail=str(ex))
    except Exception as ex:
        raise HTTPException(status_code=400, detail=str(ex))


# ---- Optional direct endpoints (easier manual testing / Swagger UI) ----

@router.post("/csv-to-json")
async def csv_to_json(
    file: UploadFile = File(...),
    user_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    try:
        result = await service.convert_file("csv", "json", file, db, user_id)
        return _respond_with_file(result)
    except Exception as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@router.post("/json-to-excel")
async def json_to_excel(
    file: UploadFile = File(...),
    user_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    try:
        result = await service.convert_file("json", "excel", file, db, user_id)
        return _respond_with_file(result)
    except Exception as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@router.post("/excel-to-json")
async def excel_to_json(
    file: UploadFile = File(...),
    user_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    try:
        result = await service.convert_file("excel", "json", file, db, user_id)
        return _respond_with_file(result)
    except Exception as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@router.post("/pdf-to-txt")
async def pdf_to_txt(
    file: UploadFile = File(...),
    user_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    try:
        result = await service.convert_file("pdf", "txt", file, db, user_id)
        return _respond_with_file(result)
    except Exception as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@router.post("/xml-to-json")
async def xml_to_json(
    file: UploadFile = File(...),
    user_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    try:
        result = await service.convert_file("xml", "json", file, db, user_id)
        return _respond_with_file(result)
    except Exception as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@router.post("/json-to-xml")
async def json_to_xml(
    file: UploadFile = File(...),
    user_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    try:
        result = await service.convert_file("json", "xml", file, db, user_id)
        return _respond_with_file(result)
    except Exception as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@router.get("/history")
def get_history(limit: int = 100, db: Session = Depends(get_db)):
    from services.history_service import HistoryService

    history_service = HistoryService()
    records = history_service.get_all(db, limit=limit)
    return [
        {
            "id": r.id,
            "source_format": r.source_format,
            "target_format": r.target_format,
            "original_file_name": r.original_file_name,
            "converted_file_name": r.converted_file_name,
            "status": r.status,
            "error_message": r.error_message,
            "created_by": r.created_by,
            "created_date": r.created_date.isoformat() if r.created_date else None,
        }
        for r in records
    ]
