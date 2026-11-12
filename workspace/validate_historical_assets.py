from pathlib import Path
from PIL import Image
import json
folder=Path.cwd()/"working"/"historical-figures-photographed"/"assets"
out=[]
for p in sorted(folder.iterdir()):
    if not p.is_file(): continue
    try:
        with Image.open(p) as im:
            im.verify()
        with Image.open(p) as im:
            out.append({"file":p.name,"valid":True,"format":im.format,"dimensions":[im.width,im.height],"bytes":p.stat().st_size})
    except Exception as e:
        out.append({"file":p.name,"valid":False,"bytes":p.stat().st_size,"error":str(e)})
print(json.dumps(out,indent=2))
