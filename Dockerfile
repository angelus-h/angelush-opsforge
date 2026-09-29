# ==============================================================================
# AngelusH SRE Hub Dockerfile
# Containerizes the Streamlit dashboard and its external tool scripts.
# ==============================================================================

# Use official Python 3.11 slim image as the base
FROM registry.access.redhat.com/ubi9/python-311:latest

# Switch to root to install system dependencies
USER root

# Install system dependencies (e.g., git, curl, kubectl, oc)
# Note: kubectl/oc are needed for the OpenShift/Tekton tools to work inside the container
RUN dnf install -y --nodocs git curl jq && \
    dnf clean all

# Download and install oc / kubectl CLI tools
RUN curl -LO https://mirror.openshift.com/pub/openshift-v4/clients/ocp/latest/openshift-client-linux.tar.gz && \
    tar -xvzf openshift-client-linux.tar.gz -C /usr/local/bin/ oc kubectl && \
    rm -f openshift-client-linux.tar.gz

# Download and install Tekton (tkn) CLI
RUN curl -LO https://github.com/tektoncd/cli/releases/download/v0.35.1/tkn_0.35.1_Linux_x86_64.tar.gz && \
    tar -xvzf tkn_0.35.1_Linux_x86_64.tar.gz -C /usr/local/bin/ tkn && \
    rm -f tkn_0.35.1_Linux_x86_64.tar.gz

# Switch back to the non-root user provided by the UBI image
USER 1001

# Set the working directory
WORKDIR /opt/app-root/src

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY --chown=1001:1001 . .

# Ensure data directories exist and have proper permissions
RUN mkdir -p /opt/app-root/src/state /opt/app-root/src/state/logs /opt/app-root/src/state/maps /opt/app-root/src/state/bundles /opt/app-root/src/state/digests

# Expose the Streamlit port
EXPOSE 8501

# Add healthcheck to ensure Streamlit is running
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Start the Streamlit application
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
