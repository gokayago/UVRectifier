bl_info = {
    "name": "Texture Extractor to Atlas (Auto Selected Faces)",
    "author": "ChatGPT (prototype)",
    "version": (0, 3),
    "blender": (5, 0, 0),
    "location": "UV Editor / Sidebar > Texture Extraction",
    "description": "Automatically extract trapezoidal regions from a source image using selected faces and paste rectified patches into a destination image.",
    "warning": "Prototype — test on copies of your files",
    "category": "UV",
}

import bpy
import bmesh
from mathutils import Matrix
import math

# -----------------------------
# Scene properties (UI lists)
# -----------------------------

def enum_images(self, context):
    items = []
    for img in bpy.data.images:
        items.append((img.name, img.name, ""))
    if not items:
        items = [("NONE", "(no images)", "")]
    return items


def enum_uvmaps(self, context):
    items = []
    obj = context.object
    if not obj or obj.type != 'MESH' or obj.data is None:
        return [("NONE", "(no mesh)", "")]
    for uv in obj.data.uv_layers:
        items.append((uv.name, uv.name, ""))
    if not items:
        items = [("NONE", "(no uv maps)", "")]
    return items


bpy.types.Scene.te_src_image = bpy.props.EnumProperty(
    name="Source Image",
    items=enum_images
)

bpy.types.Scene.te_dst_image = bpy.props.EnumProperty(
    name="Destination Image",
    items=enum_images
)

bpy.types.Scene.te_src_uv = bpy.props.EnumProperty(
    name="Source UV Map",
    items=enum_uvmaps
)

bpy.types.Scene.te_dst_uv = bpy.props.EnumProperty(
    name="Destination UV Map",
    items=enum_uvmaps
)

# -----------------------------
# Utility functions
# -----------------------------

def uv_to_pixel(uv, image):
    w, h = image.size
    x = max(0.0, min(1.0, uv[0])) * (w - 1)
    y = max(0.0, min(1.0, uv[1])) * (h - 1)
    return (x, y)


def read_image_pixels(image):
    if image is None:
        return None, 0
    channels = image.channels if hasattr(image, 'channels') else 4
    return image.pixels, channels


def sample_bilinear_from_buffer(buf, channels, w, h, fx, fy):
    if fx < 0.0 or fy < 0.0 or fx > (w - 1) or fy > (h - 1):
        return [0.0] * channels
    x0 = int(math.floor(fx))
    y0 = int(math.floor(fy))
    x1 = x0 + 1
    if x1 >= w:
        x1 = w - 1
    y1 = y0 + 1
    if y1 >= h:
        y1 = h - 1
    sx = fx - x0
    sy = fy - y0

    base00 = (y0 * w + x0) * channels
    base10 = (y0 * w + x1) * channels
    base01 = (y1 * w + x0) * channels
    base11 = (y1 * w + x1) * channels

    out = [0.0] * channels
    inv_sx = 1.0 - sx
    inv_sy = 1.0 - sy

    for c in range(channels):
        p00 = buf[base00 + c]
        p10 = buf[base10 + c]
        p01 = buf[base01 + c]
        p11 = buf[base11 + c]
        a = p00 * inv_sx + p10 * sx
        b = p01 * inv_sx + p11 * sx
        out[c] = a * inv_sy + b * sy
    return out


# Homography (DLT) for 4 points (maps src_pts -> dst_pts)
def compute_homography(src_pts, dst_pts):
    A = []
    for (x, y), (u, v) in zip(src_pts, dst_pts):
        A.append([-x, -y, -1, 0, 0, 0, x * u, y * u, u])
        A.append([0, 0, 0, -x, -y, -1, x * v, y * v, v])
    M = []
    b = []
    for row in A:
        M.append(row[:8])
        b.append(-row[8])
    try:
        h = solve_linear_system(M, b)
    except Exception:
        return None
    h.append(1.0)
    H = Matrix([[h[0], h[1], h[2]],[h[3], h[4], h[5]],[h[6], h[7], h[8]]])
    return H


def solve_linear_system(A, b):
    n = len(A)
    M = [list(map(float, row)) for row in A]
    B = list(map(float, b))
    for k in range(n):
        maxrow = max(range(k, n), key=lambda r: abs(M[r][k]))
        if abs(M[maxrow][k]) < 1e-12:
            raise Exception('Singular matrix')
        M[k], M[maxrow] = M[maxrow], M[k]
        B[k], B[maxrow] = B[maxrow], B[k]
        pivot = M[k][k]
        for j in range(k, n):
            M[k][j] /= pivot
        B[k] /= pivot
        for i in range(n):
            if i == k: continue
            factor = M[i][k]
            if factor == 0: continue
            for j in range(k, n):
                M[i][j] -= factor * M[k][j]
            B[i] -= factor * B[k]
    return B


# -----------------------------
# Main operator: extract selected faces automatically
# -----------------------------

class TE_OT_extract_selected_faces(bpy.types.Operator):
    bl_idname = "te.extract_selected_faces"
    bl_label = "Extract From Selected Faces"
    bl_description = "Use all selected faces on the active mesh and extract patches from source image into destination image"

    def execute(self, context):
        scn = context.scene
        src_img_name = scn.te_src_image
        dst_img_name = scn.te_dst_image
        src_uv_name = scn.te_src_uv
        dst_uv_name = scn.te_dst_uv

        if src_img_name == "NONE" or dst_img_name == "NONE":
            self.report({"ERROR"}, "Select both source and destination images")
            return {'CANCELLED'}
        src_img = bpy.data.images.get(src_img_name)
        dst_img = bpy.data.images.get(dst_img_name)
        if not src_img or not dst_img:
            self.report({"ERROR"}, "Selected images not found")
            return {'CANCELLED'}

        obj = context.object
        if not obj or obj.type != 'MESH' or obj.data is None:
            self.report({"ERROR"}, "Active object must be a mesh with selected faces")
            return {'CANCELLED'}

        # Prepare pixel buffers
        src_pixels_prop, src_ch = read_image_pixels(src_img)
        dst_pixels_prop, dst_ch = read_image_pixels(dst_img)
        if src_pixels_prop is None or dst_pixels_prop is None:
            self.report({"ERROR"}, "Images must have pixel buffers (ensure they are not packed or invalid)")
            return {'CANCELLED'}
        src_buf = list(src_pixels_prop)
        dst_buf = list(dst_pixels_prop)
        sw, sh = src_img.size
        dw, dh = dst_img.size

        # Get selected faces and UVs from either edit-mode bmesh or object-mode polygons
        faces_uv_pairs = []  # list of (src_uv_list, dst_uv_list)

        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            src_layer = bm.loops.layers.uv.get(src_uv_name)
            dst_layer = bm.loops.layers.uv.get(dst_uv_name)
            if src_layer is None or dst_layer is None:
                self.report({"ERROR"}, "UV map not found on mesh (check names)")
                return {'CANCELLED'}
            for f in bm.faces:
                if not f.select:
                    continue
                if len(f.loops) != 4:
                    continue
                src_uvs = [ (loop[src_layer].uv.x, loop[src_layer].uv.y) for loop in f.loops ]
                dst_uvs = [ (loop[dst_layer].uv.x, loop[dst_layer].uv.y) for loop in f.loops ]
                faces_uv_pairs.append((src_uvs, dst_uvs))
        else:
            # object mode
            mesh = obj.data
            src_layer = mesh.uv_layers.get(src_uv_name)
            dst_layer = mesh.uv_layers.get(dst_uv_name)
            if src_layer is None or dst_layer is None:
                self.report({"ERROR"}, "UV map not found on mesh (check names)")
                return {'CANCELLED'}
            for poly in mesh.polygons:
                if not poly.select:
                    continue
                if len(poly.loop_indices) != 4:
                    continue
                loop_indices = poly.loop_indices
                src_uvs = [ (src_layer.data[i].uv[0], src_layer.data[i].uv[1]) for i in loop_indices ]
                dst_uvs = [ (dst_layer.data[i].uv[0], dst_layer.data[i].uv[1]) for i in loop_indices ]
                faces_uv_pairs.append((src_uvs, dst_uvs))

        if not faces_uv_pairs:
            self.report({"ERROR"}, "No selected quad faces found with the chosen UV maps")
            return {'CANCELLED'}

        # Process each face: compute homography and rasterize into dst buffer
        for src_uvs, dst_uvs in faces_uv_pairs:
            # convert to pixel coordinates
            src_px = [uv_to_pixel(uv, src_img) for uv in src_uvs]
            dst_px = [uv_to_pixel(uv, dst_img) for uv in dst_uvs]

            # compute homography mapping src_px -> dst_px, then invert to get dst->src
            H_src2dst = compute_homography(src_px, dst_px)
            if H_src2dst is None:
                print('Homography failed for face; skipping')
                continue
            try:
                H = H_src2dst.inverted()
            except Exception:
                print('Homography inversion failed; skipping')
                continue

            h00 = float(H[0][0]); h01 = float(H[0][1]); h02 = float(H[0][2])
            h10 = float(H[1][0]); h11 = float(H[1][1]); h12 = float(H[1][2])
            h20 = float(H[2][0]); h21 = float(H[2][1]); h22 = float(H[2][2])

            # bounding box in destination image pixel coords
            minx = int(max(0, math.floor(min(p[0] for p in dst_px))))
            maxx = int(min(dw - 1, math.ceil(max(p[0] for p in dst_px))))
            miny = int(max(0, math.floor(min(p[1] for p in dst_px))))
            maxy = int(min(dh - 1, math.ceil(max(p[1] for p in dst_px))))

            for y in range(miny, maxy + 1):
                for x in range(minx, maxx + 1):
                    px = x + 0.5
                    py = y + 0.5
                    denom = (h20 * px + h21 * py + h22)
                    if abs(denom) < 1e-8:
                        continue
                    sx = (h00 * px + h01 * py + h02) / denom
                    sy = (h10 * px + h11 * py + h12) / denom

                    # skip if outside source bounds
                    if sx < 0.0 or sy < 0.0 or sx > (sw - 1) or sy > (sh - 1):
                        continue

                    color = sample_bilinear_from_buffer(src_buf, src_ch, sw, sh, sx, sy)

                    base = (y * dw + x) * dst_ch
                    for c in range(dst_ch):
                        if c < len(color):
                            dst_buf[base + c] = color[c]
                        else:
                            dst_buf[base + c] = 1.0

        # write back to destination image in one shot
        if len(dst_buf) == len(dst_pixels_prop):
            dst_pixels_prop.foreach_set(dst_buf)
        else:
            for i, v in enumerate(dst_buf):
                dst_pixels_prop[i] = v
        dst_img.update()

        self.report({"INFO"}, f"Extracted {len(faces_uv_pairs)} face(s) into {dst_img.name}")
        return {'FINISHED'}


# -----------------------------
# UI Panel
# -----------------------------

class TE_PT_panel(bpy.types.Panel):
    bl_label = "Texture Extraction"
    bl_idname = "TE_PT_panel"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'TextureExtraction'

    def draw(self, context):
        scn = context.scene
        layout = self.layout
        layout.prop(scn, 'te_src_image')
        layout.prop(scn, 'te_src_uv')
        layout.separator()
        layout.prop(scn, 'te_dst_image')
        layout.prop(scn, 'te_dst_uv')
        layout.separator()
        row = layout.row()
        row.operator('te.extract_selected_faces', text='Extract Selected Faces')


# -----------------------------
# Register
# -----------------------------

classes = (
    TE_OT_extract_selected_faces,
    TE_PT_panel,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == '__main__':
    register()
