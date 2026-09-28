import re
import unicodedata

def remove_vietnamese_accents(text: str) -> str:
    """Normalize and strip Vietnamese diacritics for robust keyword matching."""
    nfkd = unicodedata.normalize("NFKD", str(text or ""))
    return (
        "".join(c for c in nfkd if not unicodedata.combining(c))
        .replace("đ", "d")
        .replace("Đ", "D")
    )


def translate_transcript_to_visual_action(
    transcript: str,
    *,
    video_title: str = "",
    reference_name: str = "",
    scene_index: int = 0,
    total_scenes: int = 10,
    prev_actions: list[str] | None = None,
) -> str:
    """Transform raw voiceover transcripts into rich, concrete, cinematic visual scene actions in English.
    
    Eliminates abstract philosophy, rhetorical questions, numbers, and meta-speech (e.g. CTA/subscribing)
    so Google Flow / Veo / Omni models receive clear, physically observable directives.
    """
    raw = (transcript or "").strip()
    raw_lower = raw.lower()
    norm = remove_vietnamese_accents(raw_lower)
    title_norm = remove_vietnamese_accents((video_title or "").lower())

    prev_actions = prev_actions or []
    prev_last = prev_actions[-1] if prev_actions else ""

    # 1. Detect Outro / Call-to-Action / Ending meta-speech
    cta_patterns = (
        "dang ky kenh", "nhan thich", "chia se video", "video tiep theo",
        "cam on quy vi", "theo doi kenh", "tham gia hoi vien", "de lai binh luan",
        "hen gap lai", "chuc quy vi", "tu hao viet nam", "dinh doan phan tich",
        "thau hieu hon nhan", "bam like", "nhan chuong", "ung ho kenh"
    )
    is_ending = scene_index >= max(1, total_scenes - 2) or (total_scenes > 5 and scene_index >= total_scenes - 3)
    if is_ending and any(p in norm for p in cta_patterns):
        if any(k in title_norm for k in ("quan doi", "chien tranh", "khmer", "tuong", "tran", "lich su", "viet nam")):
            return "Panoramic closing wide shot of a peaceful Vietnamese landscape and historical monument under soft golden hour sunset, solemn and inspiring atmosphere"
        else:
            return "Cinematic closing wide shot of a tranquil Vietnamese village landscape bathed in warm evening sunlight, serene peaceful atmosphere"

    # 2. Check Military / War / History Domain
    is_military = any(k in title_norm or k in norm for k in (
        "chien tranh", "khmer", "su doan", "phao binh", "xe tang", "khong quan",
        "quan doi", "chien dich", "canh dong chum", "le trong tan", "bo binh",
        "tien quan", "danh chiem", "phong thu", "chien hao", "bo chi huy",
        "tran danh", "luc luong", "vuot song", "trung doan", "tieu doan", "diem cao",
        "tong tien cong", "tap kich", "truy kich", "quan doan", "vuot bien", "ha lao"
    ))

    if is_military:
        # Priority 1: Water / River Crossing / Amphibious
        if any(k in norm for k in ("vuot song", "song", "ben pha", "hai quan", "thuyen", "tau chien", "song mekong")):
            return "Troops and military amphibious vehicles crossing a wide tropical river under dramatic cloudy skies, water reflections and ripples"

        # Priority 2: Tanks / Armored Column
        if any(k in norm for k in ("xe tang", "tang thiet giap", "mui tang", "t-54", "thiet giap", "co gioi")):
            if "tank" in prev_last.lower():
                return "Close dramatic angle of Vietnamese tank crew and mechanized infantry coordinating movement along a dusty dirt road"
            return "Armored column of T-54 tanks and military vehicles advancing across rugged terrain, dust clouds rising in morning light"

        # Priority 3: Heavy Artillery / Fortifications / Trenches
        if any(k in norm for k in ("phao binh", "tran dia phao", "cong su", "chien hao", "phong thu", "ham hao", "phao")):
            return "Heavy field artillery batteries stationed in fortified earthen bunkers and trenches, camouflaged strategic military outpost"

        # Priority 4: Mountain / Jungle / Hill Outpost Combat
        if any(k in norm for k in ("diem cao", "doi", "nui", "ban xua", "canh dong chum", "diem cao 1", "diem cao 12", "choi cao", "cao diem")):
            return "Vietnamese infantry squad in vintage 1970s military uniforms advancing tactically up a misty jungle mountain ridge and rocky peak"

        # Priority 5: Air Force / Aerial / Bombing
        if any(k in norm for k in ("khong quan", "may bay", "nem bom", "phi doi", "tren khong", "tiem kich")):
            return "Military transport and fighter aircraft flying low over misty mountain peaks and jungle canopy"

        # Priority 6: Rapid March / Blitzkrieg / Breakthrough
        if any(k in norm for k in ("14 ngay", "muoi bon ngay", "chop nhoang", "thoc sau", "mo duong", "tien sau", "toc do", "quet sach")):
            return "Dynamic wide shot of Vietnamese mechanized infantry column advancing swiftly across misty countryside at dawn, epic military atmosphere"

        # Priority 7: High Command / Generals / Strategy
        if any(k in norm for k in ("bo chi huy", "tuong", "chi huy", "le trong tan", "vo nguyen giap", "hop", "ban do", "chien luoc", "tinh toan", "nghe thuat")):
            if "map table" in prev_last.lower():
                return "Military commander in green uniform observing the battlefield through binoculars from a forward observation post"
            return "Vietnamese military commanders in green uniforms gathered around a large tactical map table inside a tent, serious strategic planning by lantern light"

        # Priority 8: Enemy Troops / Encampment
        if any(k in norm for k in ("khmer do", "doi phuong", "linh ao den", "pol pot", "ben kia bien gioi")):
            return "Historic 1970s tropical border outpost with barbed wire, wooden watchtower, and dense misty jungle background"

        # Priority 9: Victory / Operations / Aftermath
        if any(k in norm for k in ("thang loi", "ket qua", "giai phong", "giu vung", "loai khoi", "giai thoat")):
            return "Historic battlefield landscape after the operation, smoke clearing over green hills, soldiers standing resolute on high ground with flag in breeze"

        # Military General Action with diversity
        military_variations = [
            "Vietnamese infantry soldiers in historic 1970s uniforms patrolling through a lush tropical landscape with rifles and gear",
            "Column of soldiers marching through a misty morning bamboo forest near the border",
            "Field communications outpost with radio operators and soldiers coordinating tactical maneuvers",
            "Soldiers stationed in fortified defensive positions overlooking a wide valley"
        ]
        return military_variations[scene_index % len(military_variations)]

    # 3. Rural / Psychology / Family / Narrative Domain (Đinh Đoàn / Thấu Hiểu Hôn Nhân)
    # Couple / Husband & Wife / Dialogue
    if any(k in norm for k in ("vo chong", "hai nguoi", "tro chuyen", "dam dao", "hon nhan", "gia dinh", "tam su", "cai va", "chia se", "nguoi yeu")):
        return "A couple sitting together in a cozy traditional wooden living room, engaged in an earnest emotional conversation with soft window lighting"

    # Tea / Senior / Solitary reflection
    if any(k in norm for k in ("uong tra", "am tra", "tach tra", "hien nha", "tram ngam", "suy nghi", "bac ba", "ong cu", "ba cu", "tuoi gia", "uong nuoc")):
        if "tea" in prev_last.lower():
            return "An elderly Vietnamese person looking pensively through a wooden window into the peaceful garden outside"
        return "An elderly Vietnamese person with weathered gentle face sitting on a rustic wooden porch, holding a steaming tea cup in morning sunlight"

    # Storm / Crisis / Disaster / Hardship
    if any(k in norm for k in ("bao", "mua", "lu lut", "tan hoang", "ngap", "song gio", "kho khan", "mat mat", "giong bao")):
        return "Dramatic heavy rainstorm over a rural Vietnamese village, rain pouring on wooden roofs and flooded green fields, moody lighting"

    # Kitchen / Cooking / Family Hearth
    if any(k in norm for k in ("bep", "nau com", "bua com", "noi com", "am cung", "mam com")):
        return "Warm rustic kitchen inside a traditional house with clay cooking stove and gentle steam rising, soft amber hearth light"

    # Farming / Field / Agriculture
    if any(k in norm for k in ("dong ruong", "canh dong", "lang que", "cay lua", "lam vuon", "bamboo", "nong dan", "cay cay", "ruong lua")):
        return "Farmers wearing conical hats working diligently in emerald green rice paddy fields, picturesque Vietnamese countryside"

    # Traditional Market / Village path
    if any(k in norm for k in ("cho", "cho que", "con duong", "duong lang", "xe dap")):
        return "Bustling traditional Vietnamese village market with rustic wooden stalls, colorful produce, and villagers in morning light"

    # 4. Modern / Investigation / Office / Science
    if any(k in norm for k in ("dieu tra", "ho so", "van phong", "tai lieu", "may tinh", "nghien cuu", "canh sat", "kham nghiem")):
        return "Focused investigator examining case documents and photographs on a wooden desk under a warm vintage desk lamp"

    # 5. Default Narrative Fallback with rotation for visual variety
    narrative_variations = [
        "A peaceful Vietnamese village setting with traditional wooden houses, bamboo groves, and golden morning sunlight",
        "A contemplative character walking slowly along a winding village dirt path beside lush greenery",
        "Cozy interior of a traditional Vietnamese home with warm ambient lighting and nostalgic atmosphere",
        "Scenic panoramic view of Vietnamese countryside with rice paddies and distant misty mountains"
    ]
    return narrative_variations[scene_index % len(narrative_variations)]
