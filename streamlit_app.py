import streamlit as st
import pandas as pd
import re
import plotly.express as px

# ─────────────────────────────────────────────────────
# Streamlit App: PA Response Generator
# ─────────────────────────────────────────────────────

st.set_page_config(page_title="PA Response Generator", layout="wide")
st.title("Prior-Authorization (PA) Response Generator")

st.markdown(
    """
**Workflow**
1. Upload a **CSV** or **Excel** patient dataset (must match sample columns).
2. App auto-generates answers to 13 PA-form questions.
3. Interactive summary visuals appear below.
4. Data anomalies are flagged with explanations.
5. Download results as CSV.
    """
)

# ─────────────────────────────────────────────────────
# Helper functions & rule sets
# ─────────────────────────────────────────────────────

def std(text: str) -> str:
    return str(text).lower()

KEYWORDS = {
    "Upper Extremity": ["shoulder","elbow","wrist","hand","arm"],
    "Lower Extremity": ["hip","thigh","knee","ankle","foot","leg"],
    "Spine/Trunk":     ["spine","back","lumbar","thoracic","cervical"],
    "Head/Face/Jaw":   ["head","face","jaw","tmj","concussion"],
}
PREFIX_BUCKETS = {
    "Upper Extremity": ("M75","S4","S43","S45"),
    "Lower Extremity": ("M17","S83","S82","S86"),
    "Spine/Trunk":     ("M54","S23","S33","M51","M50"),
    "Head/Face/Jaw":   ("S02","S06"),
}
ICD_LATERALITY = {"1":"Right","2":"Left","3":"Bilateral"}
SURGERY_KW = {
    "Joint Replacement Surgery": ["replacement","arthroplasty","tkr"],
    "Arthroscopic/Minimally Invasive Joint Surgery": ["arthroscopic","arthroscopy","scope"],
    "Spine Surgery": ["laminectomy","fusion","discectomy"],
    "Fracture/Trauma Repair": ["fracture","orif","hardware","fixation"],
}
SPECIAL_TESTS = ["lachman","hawkins","phalen","drawer","apprehension"]

def any_kw(text: str, kws: list[str]) -> bool:
    t = std(text)
    return any(k in t for k in kws)

def fmt_date(val) -> str:
    if pd.isna(val) or val == "":
        return ""
    try:
        return pd.to_datetime(val).strftime("%d-%m-%Y")
    except:
        return str(val)

# ─── Q7: Body Part ─────────────────────────────────────────

def body_part(row):
    matches = set()
    icd = str(row.get("Primary_Diagnosis_Code",""))
    blob = (str(row.get("Diagnosis_Description","")) + " " + str(row.get("Assessment",""))).lower()
    for cat,pref in PREFIX_BUCKETS.items():
        if icd.startswith(pref):
            matches.add(cat)
    for cat,kws in KEYWORDS.items():
        if any_kw(blob,kws):
            matches.add(cat)
    if len(matches)==1:
        return matches.pop()
    if len(matches)>1:
        return "Multiple Areas / Systemic"
    return ""

# ─── Q8: Side ────────────────────────────────────────────────

def side(row, part):
    icd = str(row.get("Primary_Diagnosis_Code",""))
    if len(icd)>=5 and icd[4] in ICD_LATERALITY:
        return ICD_LATERALITY[icd[4]]
    blob = " ".join(str(row.get(c,"")).lower() for c in ["Diagnosis_Description","Assessment","Range_of_Motion","Strength"])
    if re.search(r"\bbilat(er(al)?)?\b|\bboth\b",blob):
        return "Bilateral"
    if "left" in blob:
        return "Left"
    if "right" in blob:
        return "Right"
    if part in ["Spine/Trunk","Head/Face/Jaw"]:
        return "Not Applicable"
    return ""

# ─── Q12: Surgery Type ───────────────────────────────────────

def surgery_type(row):
    if std(row.get("Had_Surgery","")) not in ("yes","y","true","1"): return ""
    blob = " ".join(str(row.get(c,"")).lower() for c in ["Diagnosis_Description","Assessment","Justification_for_PT"])
    for cat,kws in SURGERY_KW.items():
        if any_kw(blob,kws):
            return cat
    return "Other Orthopedic/Soft Tissue Surgery"

# ─── Q13: Objective Findings ─────────────────────────────────

def findings(row):
    out=[]
    rom = std(row.get("Range_of_Motion",""))
    stren= std(row.get("Strength",""))
    asses= std(row.get("Assessment",""))
    if any(x in rom for x in ["limited","restriction"]) or "rom" in rom:
        out.append("Restricted ROM")
    if any(x in stren for x in ["/5","weak","deficit"]):
        out.append("Strength Deficits")
    if any(x in asses for x in ["pain","tender","swelling"]):
        out.append("Pain/Swelling")
    if any(x in asses for x in ["gait","balance"]):
        out.append("Balance/Gait Impaired")
    if any_kw(asses,SPECIAL_TESTS):
        out.append("Positive Special Tests")
    return "; ".join(out)

# ─── Core generator ──────────────────────────────────────────

def generate(df):
    rows, anomalies = [],[]
    for _,r in df.iterrows():
        part = body_part(r)
        sd   = side(r,part)
        surg = std(r.get("Had_Surgery","")) in ("yes","y","true","1")
        rec={
            "Patient_ID":r.get("Patient_ID"),
            "Name":r.get("Patient_Name"),
            "DOB":fmt_date(r.get("Date_of_Birth")),
            "Payer":r.get("Insurance_Payer"),
            "Policy#":r.get("Policy_Number"),
            "Ref_MD":r.get("Referring_Physician"),
            "ICD10":r.get("Primary_Diagnosis_Code"),
            "Body_Part":part,
            "Side":sd,
            "Injury_Date":fmt_date(r.get("Date_of_Injury_Onset")),
            "Had_Surgery":"Yes" if surg else "No",
            "Surgery_Date":fmt_date(r.get("Date_of_Surgery")) if surg else "",
            "Surgery_Type":surgery_type(r),
            "Objective_Findings":findings(r)
        }
        rows.append(rec)
        issues=[]
        if rec["Body_Part"]=="": issues.append("Missing Body_Part")
        if rec["Side"]=="": issues.append("Missing Side")
        if surg and rec["Surgery_Date"]=="": issues.append("Surgery flagged without date")
        if issues:
            anomalies.append({"Issue":"; ".join(issues),**rec})
    return pd.DataFrame(rows), pd.DataFrame(anomalies)

# ─────────────────────────────────────────────────────────────
# UI Workflow
# ─────────────────────────────────────────────────────────────

uploaded = st.file_uploader("Upload patient dataset", type=["csv","xlsx","xls"])
if uploaded:
    df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
    results, anoms = generate(df)
    st.success(f"Processed {len(results)} patients ✅")

    st.subheader("PA Responses")
    st.dataframe(results, use_container_width=True, height=350)
    st.download_button("Download CSV", results.to_csv(index=False).encode(), "pa_responses.csv", mime="text/csv")

    st.markdown("---")
    st.subheader("Summary Visuals")
    c1,c2,c3 = st.columns(3)
    with c1:
        bp_df = results["Body_Part"].replace("","Unknown").value_counts().reset_index()
        bp_df.columns = ["Body_Part","Count"]
        fig1 = px.bar(bp_df, x="Body_Part", y="Count", title="Body Part Distribution")
        fig1.update_layout(margin=dict(l=20,r=20,t=30,b=20))
        st.plotly_chart(fig1, use_container_width=True)
    with c2:
        sd_df = results["Side"].replace("","Unknown").value_counts().reset_index()
        sd_df.columns=["Side","Count"]
        fig2 = px.pie(sd_df, names="Side", values="Count", title="Affected Side", hole=0.4)
        fig2.update_layout(margin=dict(l=20,r=20,t=30,b=20))
        st.plotly_chart(fig2, use_container_width=True)
    with c3:
        sg_df = results["Had_Surgery"].value_counts().reset_index()
        sg_df.columns=["Had_Surgery","Count"]
        fig3 = px.bar(sg_df, x="Had_Surgery", y="Count", title="Surgery Yes/No")
        fig3.update_layout(margin=dict(l=20,r=20,t=30,b=20))
        st.plotly_chart(fig3, use_container_width=True)

    st.markdown("---")
    st.subheader("Data Anomalies")
    if anoms.empty:
        st.success("No anomalies detected 🎉")
    else:
        st.warning(f"{len(anoms)} anomalies found")
        st.dataframe(anoms, use_container_width=True)
else:
    st.info("Awaiting upload of patient dataset...")
