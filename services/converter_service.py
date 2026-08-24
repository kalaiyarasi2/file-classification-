import os
import shutil
import uuid

from sqlalchemy.orm import Session

from factory.converter_factory import ConverterFactory
from services.history_service import HistoryService


class ConverterService:
    def __init__(self):
        self.factory = ConverterFactory()
        self.history_service = HistoryService()
        self.upload_dir = "uploads"
        self.output_dir = "outputs"
        os.makedirs(self.upload_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)

    async def convert_file(
        self,
        source_format: str,
        target_format: str,
        file,
        db: Session,
        user_id: int = None,
    ) -> dict:
        file_id = str(uuid.uuid4())
        input_path = os.path.join(self.upload_dir, f"{file_id}_{file.filename}")
        output_ext = self._get_output_extension(target_format)
        converted_name = f"{file_id}_converted{output_ext}"
        output_path = os.path.join(self.output_dir, converted_name)

        try:
            # Save the uploaded file to disk
            with open(input_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            # Find and run the correct converter
            converter = self.factory.get_converter(source_format, target_format)
            converter.convert(input_path, output_path)

            # Log success
            self.history_service.save(
                db=db,
                source_format=source_format,
                target_format=target_format,
                original_file_name=file.filename,
                converted_file_name=converted_name,
                status="SUCCESS",
                user_id=user_id,
            )

            return {
                "success": True,
                "output_path": output_path,
                "download_name": f"converted{output_ext}",
                "content_type": self._get_content_type(target_format),
            }

        except Exception as ex:
            # Log failure, then re-raise so the controller can return a 400
            self.history_service.save(
                db=db,
                source_format=source_format,
                target_format=target_format,
                original_file_name=getattr(file, "filename", None),
                converted_file_name=None,
                status="FAILED",
                error_message=str(ex),
                user_id=user_id,
            )
            raise

    def _get_output_extension(self, target_format: str) -> str:
        return {
            "json": ".json",
            "excel": ".xlsx",
            "xlsx": ".xlsx",
            "txt": ".txt",
            "xml": ".xml",
        }.get(target_format.lower(), ".txt")

    def _get_content_type(self, target_format: str) -> str:
        return {
            "json": "application/json",
            "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "txt": "text/plain",
            "xml": "application/xml",
        }.get(target_format.lower(), "application/octet-stream")
