from abc import ABC, abstractmethod


class BaseConverter(ABC):
    """
    Every conversion type implements this interface.
    New conversions can be added by subclassing this without touching
    any other part of the system.
    """

    source_format: str
    target_format: str

    @abstractmethod
    def convert(self, input_path: str, output_path: str) -> str:
        """Convert input_path -> output_path. Returns output_path."""
        pass
