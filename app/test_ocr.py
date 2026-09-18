from app.ocr_service import extract_text

image = "uploads/test.jpg"

print("========== DÉBUT OCR ==========")

texte = extract_text(image)

print("========== TEXTE OCR ==========")
print(texte)

print("========== FIN OCR ==========")