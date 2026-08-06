import json
import xmltodict
from converters.base_converter import BaseConverter


class XmlToJsonConverter(BaseConverter):
    source_format = "xml"
    target_format = "json"

    def convert(self, input_path: str, output_path: str) -> str:
        with open(input_path, "r", encoding="utf-8") as f:
            data = xmltodict.parse(f.read())

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        return output_path
