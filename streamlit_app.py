import streamlit as st
import pandas as pd
import re
from io import BytesIO

st.set_page_config(page_title="PA Response Generator", layout="wide")

st.title("Prior-Authorization (PA) Response Generator")
st.write("Upload a **CSV** or **Excel** file in the same column format as the notional patient dataset. The app will auto-generate answers for the 13 PA questions and let you download the results.")

# --------------------------- Helper functions ---------------------------

def standardize(text: str) -> str:
    return str(text).lower()

KEYWORDS = {
    "Upper Extremity": ["shoulder", "elbow", "wrist", "hand", "arm", "rotator", "carpal", "biceps", "triceps"],
    "Lower Extremity": ["hip", "thigh", "knee", "ankle", "foot", "leg", "acl", "mcl", "lcl", "pcl", "quadriceps", "hamstring"],
    "Spine/Trunk": ["spine", "back", "lumbar", "thoracic", "cervical", "trunk", "sacral", "pelvis"],
    "Head/Face/Jaw": ["head", "face", "jaw", "tmj", "concussion", "skull", "temporal", "mandible"],
}
UPPER_CODES = ("M75", "S4", "S43", "S45")
LOWER_CODES = ("M17", "S83", "S82", "S86")
SPINE_CODES = ("M54", "S23", "S33", "M51", "M50")
HEAD_CODES = ("S02", "S06")

SURGERY_KEYWORDS = {
    "Joint Replacement Surgery": ["replacement", "arthroplasty", "tkr", "thr", "total knee", "total hip"],
    "Arthroscopic/Minimally Invasive Joint Surgery": ["arthroscopic", "arthroscopy", "scope"],
    "Spine Surgery": ["spine surgery", "laminectomy", "fusion", "discectomy"],
    "Fracture/Trauma Repair": ["fracture", "orif", "fixation", "hardware", "repair"],
}


def match_keywords(text: str, keywords: list[str]) -> bool:
    t = standardize(text)
    return any(kw in t for kw in keywords)


def determine_body_part(row: pd.Series) -> str:
    matches = set()
    diag_code = str(row.get("Primary_Diagnosis_Code", ""))
    desc = " ".join(
        [
            str(row.get("Diagnosis_Description", "")),
            str(row.get("Assessment", "")),
        ]
    ).lower()

    if diag_code.startswith(UPPER_CODES):
        matches.add("Upper Extremity")
    if diag_code.startswith(LOWER_CODES):
        matches.add("Lower Extremity")
    if diag_code.startswith(SPINE_CODES):
        matches.add("Spine/Trunk")
    if diag_code.startswith(HEAD_CODES):
        matches.add("Head/Face/Jaw")

    for category, kws in KEYWORDS.items():
        if match_keywords(desc, kws):
            matches.add(category)

    if len(matches) == 1:
        return matches.pop()
    if len(matches) > 1:
        return "Multiple Areas / Systemic"
    return ""


def determine_side(row: pd.Series) -> str:
    text = " ".join(
        [
            str(row.get(col, ""))
            for col in [
                "Diagnosis_Description",
                "Assessment",
                "Range_of_Motion",
                "Strength",
            ]
        ]
    ).lower()

    if re.search(r"\bbilateral\b|\bboth\b|\bbilat\b", text):
        return "Bilateral"
    if "left" in text:
        return "Left"
    if "right" in text:
        return "Right"
    if determine_body_part(row) in ["Spine/Trunk", "Head/Face/Jaw"]:
        return "Not Applicable"
    return ""


def determine_surgery_type(row: pd.Series) -> str:
    if str(row.get("Had_Surgery", "")).lower() not in ("yes", "y", "true", "1"):
        return ""
    text = " ".join(
        [
            str(row.get("Diagnosis_Description", "")),
            str(row.get("Assessment", "")),
            str(row.get("Justification_for_PT", "")),
        ]
    ).lower()
    for cat, kws in SURGERY_KEYWORDS.items():
        if match_keywords(text, kws):
            return cat
    return "Other Orthopedic/Soft Tissue Surgery"


def determine_objective_findings(row: pd.Series) -> str:
    findings = []
    rom_text = str(row.get("Range_of_Motion", "")).lower()
    strength_text = str(row.get("Strength", "")).lower()
    assessment_text = str(row.get("Assessment", "")).lower()

    if any(w in rom_text for w in ["limited", "restriction"]) or "rom" in rom_text:
        findings.append("Restricted Range of Motion (ROM)")
    if any(term in strength_text for term in ["/5", "weak", "deficit"]):
        findings.append("Strength Deficits")
    if any(w in assessment_text for w in ["pain", "tender", "swelling"]):
        findings.append("Pain/Tenderness/Swelling")
    if any(w in assessment_text for w in ["gait", "balance"]):
        findings.append("Impaired Balance or Gait")
    special_tests = [
        "lachman",
        "hawkins",
        "phalen",
        "empty can",
        "speed",
        "drawer",
        "apprehension",
    ]
    if any(w in assessment_text for w in special_tests):
        findings.append("Positive Special Tests / Other Clinical Signs")
    return "; ".join(findings)


def format_date(dt) -> str:
    if pd.isna(dt):
        return ""
    return pd.to_datetime(dt).strftime("%d-%m-%Y")


def generate_responses(df: pd.DataFrame) -> pd.DataFrame:
    records = []
    for _, row in df.iterrows():
        rec = {
            "Patient_ID": row.get("Patient_ID"),
            "1": row.get("Patient_Name"),
            "2": format_date(row.get("Date_of_Birth")),
            "3": row.get("Insurance_Payer"),
            "4": row.get("Policy_Number"),
            "5": row.get("Referring_Physician"),
            "6": row.get("Primary_Diagnosis_Code"),
            "7": determine_body_part(row),
            "8": determine_side(row),
            "9": format_date(row.get("Date_of_Injury_Onset")),
        }
        has_surgery = str(row.get("Had_Surgery", "")).lower() in ("yes", "y", "true", "1")
        rec["10"] = "Yes" if has_surgery else "No"
        rec["11"] = format_date(row.get("Date_of_Surgery")) if has_surgery else ""
        rec["12"] = determine_surgery_type(row)
        rec["13"] = determine_objective_findings(row)
        records.append(rec)
    out = pd.DataFrame(records)
    return out[["Patient_ID"] + [str(i) for i in range(1, 14)]]

# --------------------------- Streamlit UI ---------------------------

uploaded = st.file_uploader("Upload patient dataset (CSV or Excel)", type=["csv", "xlsx", "xls"], accept_multiple_files=False)

if uploaded is not None:
    try:
        if uploaded.name.endswith(".csv"):
            df_patients = pd.read_csv(uploaded)
        else:
            df_patients = pd.read_excel(uploaded)

        st.success(f"Loaded {len(df_patients)} patient records.")

        responses_df = generate_responses(df_patients)
        st.subheader("Generated PA Responses")
        st.dataframe(responses_df, use_container_width=True)

        # Download button
        csv_bytes = responses_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download CSV",
            data=csv_bytes,
            file_name="pa_responses.csv",
            mime="text/csv",
        )
    except Exception as e:
        st.error(f"Error processing the file: {e}")
else:
    st.info("Awaiting file upload…")
