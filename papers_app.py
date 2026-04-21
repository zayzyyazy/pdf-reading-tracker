import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import streamlit as st
import os
import subprocess

st.set_page_config(page_title="PDF Knowledge System")

st.title("PDF Knowledge System")

RAW_DIR = "data/raw"
KNOWLEDGE_DIR = "knowledge/papers"

# Create tabs
tab1, tab2, tab3 = st.tabs(["Process", "Search", "Insights"])

# ------------------------
# PROCESS TAB
# ------------------------
with tab1:
    st.header("Upload & Process PDFs")

    uploaded_files = st.file_uploader(
        "Drag & drop PDFs here",
        type=["pdf"],
        accept_multiple_files=True
    )

    if uploaded_files:
        os.makedirs(RAW_DIR, exist_ok=True)

        for file in uploaded_files:
            file_path = os.path.join(RAW_DIR, file.name)
            with open(file_path, "wb") as f:
                f.write(file.read())

        st.success(f"{len(uploaded_files)} files uploaded")

    if st.button("Process uploaded PDFs"):
        result = subprocess.run(
            ["python3", "process_papers.py"],
            capture_output=True,
            text=True
        )

        st.text(result.stdout if result.stdout else "No output.")
        if result.stderr:
            st.error(result.stderr)

    st.subheader("Processed Papers")

    if os.path.exists(KNOWLEDGE_DIR):
        for root, dirs, files in os.walk(KNOWLEDGE_DIR):
            for file in files:
                if file.endswith(".md"):
                    st.write(f"📄 {file}")

# ------------------------
# SEARCH TAB
# ------------------------
with tab2:
    st.header("Search")
    st.write("Search will be connected next.")

# ------------------------
# INSIGHTS TAB
# ------------------------
with tab3:
    st.header("Content Insights")
    st.write("Insights will be connected next.")
