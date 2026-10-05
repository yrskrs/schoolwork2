"""Smaller API bodies without truncating evidence or reducing image resolution."""
import base64
import hashlib
import io
import json

from PIL import Image

OPENROUTER_MAX_REQUEST_BYTES = 7_500_000


def encode_payload(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def image_signature(image, normalize=False):
    """Match evidence normalized at the existing 1800px extraction resolution."""
    image = image.copy()
    if normalize:
        image.thumbnail((1800, 1800))
    image = image.convert('RGBA')
    return hashlib.sha256(str(image.size).encode() + image.tobytes()).hexdigest()


def _pdf_covered_images(raw):
    """Only fully painted opaque images with sufficient PDF display resolution.

    Unknown clipping, masks, transparency and small figures retain their
    independent originals. Embedded resources alone are not proof of visibility.
    """
    from pypdf import PdfReader
    from pypdf.generic import ContentStream
    reader = PdfReader(io.BytesIO(raw))
    covered, decoded = set(), {}
    candidates = []

    def resolve(value):
        return value.get_object() if hasattr(value,'get_object') else value

    def transform(matrix, x, y):
        a, b, c, d, e, f = matrix
        return a*x+c*y+e, b*x+d*y+f

    def bounds(matrix, rect):
        x0, y0, x1, y1 = rect
        points = [transform(matrix, x, y) for x, y in [(x0,y0),(x0,y1),(x1,y0),(x1,y1)]]
        return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)

    def compose(current, incoming):
        a,b,c,d,e,f = incoming
        ca,cb,cc,cd,ce,cf = current
        return ca*a+cc*b, cb*a+cd*b, ca*c+cc*d, cb*c+cd*d, ca*e+cc*f+ce, cb*e+cd*f+cf

    def intersect(first, second):
        if first is None or second is None:
            return None
        return max(first[0],second[0]), max(first[1],second[1]), min(first[2],second[2]), min(first[3],second[3])

    def inside(rect, clip):
        return clip is not None and rect[0] >= clip[0]-.01 and rect[1] >= clip[1]-.01 and rect[2] <= clip[2]+.01 and rect[3] <= clip[3]+.01

    def painted(box=None):
        # Later graphics or text can hide a resource that was painted earlier.
        candidates[:] = [(signature,rect) for signature,rect in candidates
                         if box is not None and (rect[2] <= box[0] or rect[0] >= box[2] or rect[3] <= box[1] or rect[1] >= box[3])]

    def scan(stream, resources, matrix, clip, opaque, page, image_path=None, depth=0):
        image_path = image_path or []
        resources = resolve(resources)
        if depth > 8 or stream is None:
            return
        stack, path_rect, complex_path = [], None, False
        for args, op in ContentStream(stream, reader).operations:
            if op == b'q':
                stack.append((matrix, clip, opaque))
            elif op == b'Q':
                if not stack:
                    return
                matrix, clip, opaque = stack.pop()
            elif op == b'cm':
                matrix = compose(matrix, tuple(map(float, args)))
            elif op == b're':
                if path_rect is not None:
                    complex_path = True
                x,y,w,h = map(float,args)
                path_rect = bounds(matrix,(x,y,x+w,y+h))
            elif op in (b'm',b'l',b'c',b'v',b'y',b'h'):
                complex_path = True
            elif op in (b'W',b'W*'):
                clip = intersect(clip, path_rect) if not complex_path else None
            elif op in (b'n',b'S',b's',b'f',b'F',b'f*',b'B',b'B*',b'b',b'b*'):
                if op != b'n':
                    painted(path_rect if not complex_path and op in (b'f',b'F',b'f*') else None)
                path_rect, complex_path = None, False
            elif op in (b'Tj',b'TJ',b"'",b'"',b'sh',b'INLINE IMAGE'):
                painted()
            elif op == b'gs':
                state = resolve(resolve(resources.get('/ExtGState',{})).get(args[0],{}))
                opaque = opaque and float(state.get('/ca',1)) >= 1 and state.get('/SMask','/None') == '/None'
            elif op == b'Tr' and int(args[0]) >= 4:
                clip = None
            elif op == b'BDC' and args and args[0] == '/OC':
                clip = None
            elif op == b'Do':
                obj = resolve(resources.get('/XObject',{})).get(args[0])
                if obj is None:
                    continue
                identity = (getattr(obj,'idnum',None), getattr(obj,'generation',None))
                obj = obj.get_object()
                if identity == (None,None):
                    identity = id(obj)
                if obj.get('/Subtype') == '/Form':
                    form_matrix = compose(matrix, tuple(map(float,obj.get('/Matrix',[1,0,0,1,0,0]))))
                    form_clip = intersect(clip,bounds(form_matrix,tuple(map(float,obj['/BBox'])))) if '/BBox' in obj else clip
                    scan(obj, obj.get('/Resources',resources), form_matrix, form_clip, opaque and '/Group' not in obj, page, image_path + [args[0]], depth+1)
                elif obj.get('/Subtype') == '/Image':
                    box = bounds(matrix,(0,0,1,1))
                    painted(box)
                    if not opaque or abs(matrix[1]) > .001 or abs(matrix[2]) > .001 or any(key in obj for key in ('/SMask','/Mask')):
                        continue
                    if not inside(box,clip):
                        continue
                    # Native Gemini PDF rendering retains up to 3072px per page.
                    scale = 3072 / max(float(page.cropbox.width),float(page.cropbox.height))
                    if identity not in decoded:
                        decoded[identity] = None
                        width, height = int(obj['/Width']), int(obj['/Height'])
                        mode = {'/DeviceRGB':'RGB','/DeviceGray':'L'}.get(str(obj.get('/ColorSpace')))
                        if width*height > 30_000_000 or obj.get('/BitsPerComponent') != 8 or not mode or '/Decode' in obj or obj.get('/ImageMask'):
                            continue
                        try:
                            data = obj.get_data()
                            try:
                                image = Image.open(io.BytesIO(data))
                                image.load()
                            except OSError:
                                image = Image.frombytes(mode,(width,height),data)
                            size = image.size
                            decoded[identity] = (image_signature(image, normalize=True), (min(size[0],1800), min(size[1],1800)))
                        except Exception:
                            continue
                    if decoded[identity] is None:
                        continue
                    signature, size = decoded[identity]
                    if (box[2]-box[0])*scale >= size[0] and (box[3]-box[1])*scale >= size[1]:
                        candidates.append((signature,box))

    for page in reader.pages:
        # Rotated/partially cropped pages need a separate visual original.
        if page.rotation or page.get('/Annots'):
            continue
        candidates.clear()
        try:
            scan(page.get_contents(), page['/Resources'], (1,0,0,1,0,0), tuple(map(float,page.cropbox)), True, page)
            covered.update(signature for signature,box in candidates)
        except Exception:
            # Parsing uncertainty must never remove evidence from this page.
            return set()
    return covered


def optimize_media(media, provider):
    from .ai_context import evidence_cache
    cache = evidence_cache()
    media = [dict(item) for item in media]
    optimized, seen, covered = [], {}, {}
    if provider == 'gemini':
        for item in media:
            if item['mime_type'] == 'application/pdf':
                raw = base64.b64decode(item['data'])
                key = 'pdf-visible-images-v4-' + hashlib.sha256(raw).hexdigest()
                signatures = cache.get(key)
                if signatures is None:
                    try:
                        signatures = _pdf_covered_images(raw)
                    except Exception:
                        signatures = set()
                    cache.set(key, signatures, 86400 * 7)
                for signature in signatures:
                    covered.setdefault(signature,item)
    for item in media:
        raw = base64.b64decode(item['data'])
        digest = hashlib.sha256(raw).hexdigest()
        key = 'lossless-ai-image-v1-' + digest
        prepared = cache.get(key)
        if prepared is None:
            prepared = {'mime_type':item['mime_type'], 'data':item['data'], 'signature':digest}
            if item['mime_type'].startswith('image/'):
                try:
                    with Image.open(io.BytesIO(raw)) as image:
                        # Preserve animation and exact decoded pixels, including alpha.
                        if not getattr(image,'is_animated',False):
                            prepared['signature'] = image_signature(image)
                            buffer = io.BytesIO()
                            image.save(buffer,format='WEBP',lossless=True,exact=True,method=4)
                            if buffer.tell() < len(raw):
                                prepared.update(mime_type='image/webp',data=base64.b64encode(buffer.getvalue()).decode())
                except (OSError,ValueError,Image.DecompressionBombError):
                    # A codec/optimization failure must not remove the original.
                    prepared = {'mime_type':item['mime_type'], 'data':item['data'], 'signature':digest}
            cache.set(key, prepared, 86400 * 7)
        if item['mime_type'].startswith('image/') and prepared['signature'] in covered:
            owner = covered[prepared['signature']]
            if item.get('source'):
                owner['source'] = owner.get('source','PDF') + '\nТакож містить повне зображення: ' + item['source']
            continue
        identity = (prepared['mime_type'],prepared['signature'])
        if identity in seen:
            previous = seen[identity]
            if item.get('source') and item['source'] not in previous.get('source',''):
                previous['source'] = previous.get('source','') + '\n' + item['source']
            for signature, owner in list(covered.items()):
                if owner is item:
                    covered[signature] = previous
            continue
        entry = dict(item, mime_type=prepared['mime_type'], data=prepared['data'])
        optimized.append(entry)
        seen[identity] = entry
        for signature, owner in list(covered.items()):
            if owner is item:
                covered[signature] = entry
    return optimized
