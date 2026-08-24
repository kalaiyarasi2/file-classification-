import pandas as pd
from converters.base_converter import BaseConverter


class ExcelToJsonConverter(BaseConverter):
    source_format = "excel"
    target_format = "json"

    def convert(self, input_path: str, output_path: str) -> str:
        df = pd.read_excel(input_path)
        df.to_json(output_path, orient="records", indent=4)
        return output_path
