# Legacy Streamlit front end (frozen)

The original browser UI, kept for reference. The supported app is the Qt
desktop app (`gui_app.py`); new features (PCB editor, DIW, the 3D tube
viewer, profiles, projects) exist only there.

    pip install -r legacy/streamlit/requirements.txt
    streamlit run legacy/streamlit/app.py
    pytest legacy/streamlit/tests      # its UI smoke tests (not part of the main run)
