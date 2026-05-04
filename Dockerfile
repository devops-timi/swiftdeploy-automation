# Dockerfile
# Tells Docker how to build the image for our Python API service.

# Base image 
# python:3.12-alpine is tiny 
# Keeps image under 300MB limit.
FROM python:3.12-alpine

# Create a non-root user
# Running as root inside a container is a security risk.
# We create a user called "appuser" and switch to it.
# -D means: don't create a password (it's a system user, not a login user)
RUN adduser -D appuser

# Set the working directory
# All subsequent commands run from /app inside the container.
WORKDIR /app


# Copy everything from the local app/ folder into /app inside the image.
COPY app/ .

# Create the logs directory
# Docker Compose will mount a named volume here for persistent logs.
# Created so it exists and is owned by appuser.
RUN mkdir -p /app/logs && chown -R appuser:appuser /app/logs

# Switch to the non-root user 
USER appuser

# Expose port 3000
# The actual port binding happens in docker-compose.yml.
EXPOSE 3000

# When the container starts, run main.py with Python.
CMD ["python3", "main.py"]