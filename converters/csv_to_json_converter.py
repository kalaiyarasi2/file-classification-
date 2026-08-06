import pandas as pd
from converters.base_converter import BaseConverter


class CsvToJsonConverter(BaseConverter):
    source_format = "csv"
    target_format = "json"

    def convert(self, input_path: str, output_path: str) -> str:
        df = pd.read_csv(input_path)
        df.to_json(output_path, orient="records", indent=4)
        return output_path
