from sqlalchemy.orm import Session
from database.db import ConverterHistory


class HistoryService:
    """Handles reading/writing conversion history records."""

    def save(
        self,
        db: Session,
        source_format: str,
        target_format: str,
        original_file_name: str,
        converted_file_name: str,
        status: str,
        error_message: str = None,
        user_id: int = None,
    ) -> ConverterHistory:
        record = ConverterHistory(
            source_format=source_format,
            target_format=target_format,
            original_file_name=original_file_name,
            converted_file_name=converted_file_name,
            status=status,
            error_message=error_message,
            created_by=user_id,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    def get_all(self, db: Session, limit: int = 100):
        return (
            db.query(ConverterHistory)
            .order_by(ConverterHistory.created_date.desc())
            .limit(limit)
            .all()
        )
