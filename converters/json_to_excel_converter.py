import json
import pandas as pd
from converters.base_converter import BaseConverter


class JsonToExcelConverter(BaseConverter):
    source_format = "json"
    target_format = "excel"

    def convert(self, input_path: str, output_path: str) -> str:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Normalize/flatten the JSON dynamically
        if isinstance(data, list):
            df = pd.json_normalize(data)
        elif isinstance(data, dict):
            # Check if there is a key holding a list of dictionaries (e.g., {"users": [...]})
            list_keys = [
                k for k, v in data.items()
                if isinstance(v, list) and len(v) > 0 and all(isinstance(i, dict) for i in v)
            ]
            if list_keys:
                # Use the first key that contains a list of records
                df = pd.json_normalize(data[list_keys[0]])
            else:
                df = pd.json_normalize(data)
        else:
            df = pd.DataFrame([data])

        df.to_excel(output_path, index=False)
        return output_path

