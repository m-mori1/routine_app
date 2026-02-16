from pathlib import Path
line = Path('api.py').read_text(encoding='utf-8').splitlines()[303]
start = line.index('"') + 1
end = line.rindex(')')
segment = line[start:end]
print(segment)
print([hex(ord(ch)) for ch in segment])
