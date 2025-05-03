from PIL import ImageFont

# fonts = ImageFont.get_supported_fonts()
# for font in sorted(fonts):
    # print(font)

# font = ImageFont.load_default()
# print(font)
#large_font = ImageFont.truetype(font.path, 16)



try:
	bold_font = ImageFont.truetype("arialdb.ttf", 16)
except IOError:
	print("bold some issue")
	bold_font = ImageFont.load_default()
