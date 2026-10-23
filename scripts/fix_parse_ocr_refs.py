"""一次性脚本：把 parse.py 里残留的本地 OCR 助手调用改成上游 ingest.ocr 接口。"""

path = "ingest/parse.py"
text = open(path, encoding="utf-8").read()

old = "ocr_text = _ocr_image(_render(document, index))"
new = 'ocr_text = ocr.image_to_text(_render_page(data, index), f"{name}-p{index}.png")'
print("replaced:", text.count(old))
text = text.replace(old, new)

text = text.replace("ocr_base()", "ocr.available()")
open(path, "w", encoding="utf-8", newline="\n").write(text)

for bad in ("_ocr_image", "_render(document", "ocr_base"):
    print(bad, "->", text.count(bad))
