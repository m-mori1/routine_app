from pathlib import Path
text = Path('index.html').read_text(encoding='utf-8')
start = text.index('<select name= frequency')
end = text.index('</select>', start)
print(text[start:end])
