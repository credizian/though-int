import streamlit as st
import pandas as pd
import re
from io import BytesIO

st.set_page_config(page_title="PA Response Generator", layout="wide")

st.title("Prior-Authorization (PA) Response Generator")

st.markdown(
    """
Upload a **CSV** or **Excel** file that follows the same column structure as the notional
patient dataset. The app automatically answers the 13 PA-form questions for every
patient and lets you preview and download the results.
    """
)

# ──────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────

def std(text: str) -> str:
    """Lower-cased string safe-cast."""
    return str(text).lower()

# Keyword buckets for Q7 – Affected Body Part
KEYWORDS = {
    "Upper Extremity": [
        "shoulder", "elbow", "wrist", "hand", "arm", "rotator", "carpal", "biceps", "triceps",
    ],
    "Lower Extremity": [
        "hip", "thigh", "knee", "ankle", "foot", "leg", "acl", "mcl", "lcl", "pcl", "quadriceps", "hamstring",
    ],
    "Spine/Trunk": [
        "spine", "back", "lumbar", "thoracic", "cervical", "trunk", "sacral", "pelvis",
    ],
    "Head/Face/Jaw": ["head", "face", "jaw", "tmj", "concussion", "skull"],
}

# Laterality digits in many ICD-10 musculoskeletal codes (1=R, 2=L, 3=Bilateral)
ICD_LATERALITY = {"1": "Right", "2": "Left", "3": "Bilateral"}

# ICD-10 prefix buckets for body part inference
PREFIX_BUCKETS = {
    "Upper Extremity": ("M75", "S4", "S43", "S45"),
    "Lower Extremity": ("M17", "S83", "S82", "S86"),
    "Spine/Trunk": ("M54", "S23", "S33", "M51", "M50"),
    "Head/Face/Jaw": ("S02", "S06"),
}

# Surgery type keywords for Q12
SURGERY_KEYWORDS = {
    "Joint Replacement Surgery": ["replacement", "arthroplasty", "tkr", "thr", "total knee", "total hip"],
    "Arthroscopic/Minimally Invasive Joint Surgery": ["arthroscopic", "arthroscopy", "scope"],
    "Spine Surgery": ["spine surgery", "laminectomy", "fusion", "discectomy"],
    "Fracture/Trauma Repair": ["fracture", "orif", "fixation", "hardware", "repair"],
}

SPECIAL_TESTS = [
    "lachman", "hawkins", "phalen", "empty can", "speed", "drawer", "apprehension",
]


def any_kw(text: str, kw_list: list[str]) -> bool:
    t = std(text)
    return any(kw in t for kw in kw_list)


def format_date(val) -> str:
    if pd.isna(val) or val == "":
        return ""
    try:
        return pd.to_datetime(val).strftime("%d-%m-%Y")
    except Exception:
        return str(val)

# ── Q7 – Affected Body Part ───────────────────────────────

def q7_body_part(row: pd.Series) -> str:
    matches = set()
    icd = str(row.get("Primary_Diagnosis_Code", ""))
    desc_blob = " ".join(
        [str(row.get(c, "")) for c in ["Diagnosis_Description", "Assessment"]]
    ).lower()

    # Prefix buckets
    for category, prefixes in PREFIX_BUCKETS.items():
        if icd.startswith(prefixes):
            matches.add(category)

    # Keyword buckets
    for category, kw in KEYWORDS.items():
        if any_kw(desc_blob, kw):
            matches.add(category)

    if len(matches) == 1:
        return matches.pop()
    if len(matches) > 1:
        return "Multiple Areas / Systemic"
    return ""  # unknown / not found

# ── Q8 – Affected Side ────────────────────────────────────

def q8_side(row: pd.Series, body_part: str) -> str:
    # 1. Try laterality from ICD-10 fifth or sixth character
    icd = str(row.get("Primary_Diagnosis_Code", ""))
    if len(icd) >= 5:
        digit = icd[4] if icd[4].isdigit() else icd[-1]
        if digit in ICD_LATERALITY:
            return ICD_LATERALITY[digit]

    # 2. Search text blobs
    blob = " ".join(
        [
            str(row.get(c, ""))
            for c in ["Diagnosis_Description", "Assessment", "Range_of_Motion", "Strength"]
        ]
    ).lower()
    if re.search(r"\bbilateral\b|\bboth\b|\bbilat\b", blob):
        return "Bilateral"
    if "left" in blob:
        return "Left"
    if "right" in blob:
        return "Right"

    # 3. Spine/head midline default
    if body_part in ["Spine/Trunk", "Head/Face/Jaw"]:
        return "Not Applicable"
    return ""  # unknown

# ── Q12 – Type of Surgery ────────────────────────────────

def q12_surgery_type(row: pd.Series) -> str:
    if std(row.get("Had_Surgery", "")) not in ("yes", "y", "true", "1"):
        return ""
    blob = " ".join(
        [
            str(row.get("Diagnosis_Description", "")),
            str(row.get("Assessment", "")),
            str(row.get("Justification_for_PT", "")),
        ]
    ).lower()
    for cat, kw in SURGERY_KEYWORDS.items():
        if any_kw(blob, kw):
            return cat
    return "Other Orthopedic/Soft Tissue Surgery"

# ── Q13 – Objective Findings ─────────────────────────────

def q13_findings(row: pd.Series) -> str:
    findings = []
    rom = std(row.get("Range_of_Motion", ""))
    strength = std(row.get("Strength", ""))
    assessment = std(row.get("Assessment", ""))

    if any(w in rom for w in ["limited", "restriction"]) or "rom" in rom:
        findings.append("Restricted ROM")
    if any(tok in strength for tok in ["/5", "weak", "deficit"]):
        findings.append("Strength Deficits")
    if any(w in assessment for w in ["pain", "tender", "swelling"]):
        findings.append("Pain/Swelling")
    if any(w in assessment for w in ["gait", "balance"]):
        findings.append("Balance/Gait Impaired")
    if any(w in assessment for w in SPECIAL_TESTS):
        findings.append("Positive Special Tests")
    return "; ".join(findings)

# ──────────────────────────────────────────────────────────────
# Core transformation
# ──────────────────────────────────────────────────────────────

def generate_responses(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        body_part = q7_body_part(row)
        rec = {
            "Patient_ID": row.get("Patient_ID"),
            "Patient_Name": row.get("Patient_Name"),
            "DOB": format_date(row.get("Date_of_Birth")),
            "Payer": row.get("Insurance_Payer"),
            "Policy_Number": row.get("Policy_Number"),
            "Referring_MD": row.get("Referring_Physician"),
            "Primary_ICD10": row.get("Primary_Diagnosis_Code"),
            "Body_Part": body_part,
            "Side": q8_side(row, body_part),
            "Injury_Date": format_date(row.get("Date_of_Injury_Onset")),
        }
        has_surg = std(row.get("Had_Surgery", "")) in ("yes", "y", "true", "1")
        rec["Had_Surgery"] = "Yes" if has_surg else "No"
        rec["Surgery_Date"] = format_date(row.get("Date_of_Surgery")) if has_surg else ""
        rec["Surgery_Type"] = q12_surgery_type(row)
        rec["Objective_Findings"] = q13_findings(row)
        rows.append(rec)
    return pd.DataFrame(rows)

# ──────────────────────────────────────────────────────────────
# UI Workflow
# ──────────────────────────────────────────────────────────────

uploaded = st.file_uploader("Upload patient dataset (CSV or Excel)", type=["csv", "xlsx", "xls"])

if uploaded:
    try:
        df_patients = (
            pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
        )

        st.success(f"Loaded **{len(df_patients)}** patient records.")
        out_df = generate_responses(df_patients)

        st.subheader("Generated PA Responses")
        st.dataframe(out_df, use_container_width=True)

        csv_bytes = out_df.to_csv(index=False).encode()
        st.download_button("Download CSV", csv_bytes, "pa_responses.csv", mime="text/csv")
    except Exception as e:
        st.error(f"Error processing the file: {e}")
else:
    st.info("Please upload a file to begin.")
