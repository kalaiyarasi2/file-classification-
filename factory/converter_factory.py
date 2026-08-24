from converters.csv_to_json_converter import CsvToJsonConverter
from converters.json_to_excel_converter import JsonToExcelConverter
from converters.excel_to_json_converter import ExcelToJsonConverter
from converters.pdf_to_txt_converter import PdfToTxtConverter
from converters.xml_to_json_converter import XmlToJsonConverter
from converters.json_to_xml_converter import JsonToXmlConverter


class ConverterFactory:
    """
    Selects the correct converter class based on source_format/target_format.
    Add a new conversion by registering it here — nothing else needs to change.
    """

    def __init__(self):
        self.converters = {
            "csv_to_json": CsvToJsonConverter(),
            "json_to_excel": JsonToExcelConverter(),
            "excel_to_json": ExcelToJsonConverter(),
            "pdf_to_txt": PdfToTxtConverter(),
            "xml_to_json": XmlToJsonConverter(),
            "json_to_xml": JsonToXmlConverter(),
        }

    def get_converter(self, source_format: str, target_format: str):
        key = f"{source_format.lower()}_to_{target_format.lower()}"
        if key not in self.converters:
            raise ValueError(
                f"Unsupported conversion: {source_format} to {target_format}"
            )
        return self.converters[key]

    def supported_conversions(self):
        return list(self.converters.keys())
