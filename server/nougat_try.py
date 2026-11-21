"""Read the first pages of a PDF with Nougat and write LaTeX-style markdown."""

import io
import sys
from pathlib import Path

import pymupdf
from PIL import Image
from transformers import NougatProcessor, VisionEncoderDecoderModel

MODEL = "facebook/nougat-base"
PAGES = 2


def main() -> None:
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "server/samples/2611.pdf")
    out = source.with_suffix(".nougat.md")
    processor = NougatProcessor.from_pretrained(MODEL)
    model = VisionEncoderDecoderModel.from_pretrained(MODEL)
    model.eval()
    document = pymupdf.open(source)
    pieces = []
    for index in range(min(PAGES, document.page_count)):
        pixmap = document[index].get_pixmap(dpi=144)
        image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
        inputs = processor(images=image, return_tensors="pt")
        ids = model.generate(
            **inputs,
            max_length=4096,
            bad_words_ids=[[processor.tokenizer.unk_token_id]],
        )
        text = processor.batch_decode(ids, skip_special_tokens=True)[0]
        try:
            text = processor.post_process_generation(text, fix_markdown=False)
        except AttributeError:
            pass
        pieces.append(text)
        print(f"page {index + 1} chars {len(text)}", flush=True)
    out.write_text("\n\n".join(pieces), encoding="utf-8")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
