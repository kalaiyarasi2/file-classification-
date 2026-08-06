import json
from dicttoxml import dicttoxml
from converters.base_converter import BaseConverter


class JsonToXmlConverter(BaseConverter):
    source_format = "json"
    target_format = "xml"

    def convert(self, input_path: str, output_path: str) -> str:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        xml_data = dicttoxml(data, custom_root="root", attr_type=False)

        with open(output_path, "wb") as f:
            f.write(xml_data)

        return output_path
