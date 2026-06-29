import { useCallback, useState } from "react";

export function useDropzone({
  onFiles, multiple = false, accept = ".pdf",
}: { onFiles: (f: File[]) => void; multiple?: boolean; accept?: string }) {
  const [isDragActive, setDrag] = useState(false);

  const handleFiles = useCallback((list: FileList | null) => {
    if (!list) return;
    const arr = Array.from(list).filter((f) =>
      accept.split(",").some((ext) => f.name.toLowerCase().endsWith(ext.trim()))
    );
    if (arr.length) onFiles(multiple ? arr : [arr[0]]);
  }, [accept, multiple, onFiles]);

  const rootProps = {
    onDragOver: (e: React.DragEvent) => { e.preventDefault(); setDrag(true); },
    onDragLeave: () => setDrag(false),
    onDrop: (e: React.DragEvent) => {
      e.preventDefault(); setDrag(false);
      handleFiles(e.dataTransfer.files);
    },
  };
  const inputProps = {
    type: "file" as const,
    accept,
    multiple,
    className: "hidden",
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => handleFiles(e.target.files),
  };
  return { rootProps, inputProps, isDragActive };
}
