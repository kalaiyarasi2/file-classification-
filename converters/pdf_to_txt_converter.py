import pdfplumber
from converters.base_converter import BaseConverter


class PdfToTxtConverter(BaseConverter):
    source_format = "pdf"
    target_format = "txt"

    def convert(self, input_path: str, output_path: str) -> str:
        text = []
        with pdfplumber.open(input_path) as pdf:
            for page in pdf.pages:
                text.append(page.extract_text() or "")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(text))

        return output_path
