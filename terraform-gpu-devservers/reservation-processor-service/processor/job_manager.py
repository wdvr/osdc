"""
Kubernetes Job manager for worker jobs.
Handles creation, monitoring, and cleanup of reservation processing jobs.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from kubernetes import client
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


class JobManager:
    """Manages Kubernetes Jobs for message processing."""
    
    def __init__(self, k8s_batch_api: client.BatchV1Api, k8s_core_api: client.CoreV1Api):
        """
        Initialize job manager.
        
        Args:
            k8s_batch_api: Kubernetes Batch API client
            k8s_core_api: Kubernetes Core API client
        """
        self.batch_api = k8s_batch_api
        self.core_api = k8s_core_api
        self.namespace = os.environ.get("KUBE_NAMESPACE", "gpu-controlplane")
        self.worker_image = os.environ.get("WORKER_IMAGE", "")
        self.service_account = os.environ.get("SERVICE_ACCOUNT", "reservation-processor-sa")
        self.image_pull_policy = os.environ.get("IMAGE_PULL_POLICY", "Always")
        
        if not self.worker_image:
            logger.warning("WORKER_IMAGE environment variable not set - jobs may fail")
        
        logger.info(f"JobManager initialized: namespace={self.namespace}, image={self.worker_image}")
        
    def create_job(self, msg_id: int, message: Dict[str, Any]) -> str:
        """
        Create a Kubernetes Job to process a message.
        
        Args:
            msg_id: Message ID from PGMQ
            message: Message body (the actual message content, not just ID)
        
        Returns:
            Job name
        """
        job_name = f"reservation-worker-{msg_id}"
        
        # Extract metadata for labels/annotations
        action = message.get("action", "unknown")
        user_id = message.get("user_id", "unknown")
        metadata = message.get("_metadata", {})
        retry_count = metadata.get("retry_count", 0)
        
        logger.info(f"Creating job {job_name} for action={action}, user={user_id}, retry={retry_count}")
        
        # Serialize message body to JSON for passing to worker
        import json
        message_json = json.dumps(message)
        
        # Job spec
        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=self.namespace,
                labels={
                    "app": "reservation-worker",
                    "msg_id": str(msg_id),
                    "action": action[:63] if action else "unknown",  # K8s label limit
                    "component": "worker"
                },
                annotations={
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "retry_count": str(retry_count),
                    "user_id": user_id[:255] if user_id else "unknown"
                }
            ),
            spec=client.V1JobSpec(
                # No K8s retry - we handle retries ourselves via PGMQ
                backoff_limit=0,
                
                # Timeout after 15 minutes (900 seconds)
                # K8s will kill the pod if it exceeds this time
                active_deadline_seconds=900,
                
                # Cleanup completed jobs after 1 hour (3600 seconds)
                # This prevents the cluster from filling up with job objects
                ttl_seconds_after_finished=3600,
                
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(
                        labels={
                            "app": "reservation-worker",
                            "msg_id": str(msg_id),
                            "action": action[:63] if action else "unknown"
                        }
                    ),
                    spec=client.V1PodSpec(
                        service_account_name=self.service_account,
                        restart_policy="Never",  # Never restart - let PGMQ handle retries
                        
                        # Node selection - prefer CPU nodes for orchestration
                        node_selector={"NodeType": "cpu"},
                        
                        # Tolerate CPU-only nodes
                        tolerations=[
                            client.V1Toleration(
                                key="node-role",
                                operator="Equal",
                                value="cpu-only",
                                effect="NoSchedule"
                            )
                        ],
                        
                        containers=[
                            client.V1Container(
                                name="worker",
                                image=self.worker_image,
                                image_pull_policy=self.image_pull_policy,
                                
                                # Command to run worker script with message ID
                                command=["python", "-m", "processor.worker"],
                                args=[str(msg_id)],
                                
                                # Copy environment from poller pod
                                # Add MESSAGE_BODY with the actual message content
                                env=self._get_worker_env(message_json),
                                
                                # Resource requests and limits
                                resources=client.V1ResourceRequirements(
                                    requests={
                                        "cpu": "500m",
                                        "memory": "1Gi"
                                    },
                                    limits={
                                        "cpu": "2000m",
                                        "memory": "4Gi"
                                    }
                                )
                            )
                        ]
                    )
                )
            )
        )
        
        try:
            self.batch_api.create_namespaced_job(
                namespace=self.namespace,
                body=job
            )
            logger.info(f"Successfully created job {job_name}")
            return job_name
            
        except ApiException as e:
            if e.status == 409:
                # Job already exists - this is OK (idempotent)
                logger.warning(f"Job {job_name} already exists (409 Conflict)")
                return job_name
            else:
                logger.error(f"Failed to create job {job_name}: {e.status} {e.reason}")
                logger.error(f"API response: {e.body}")
                raise
    
    def get_job_status(self, job_name: str) -> Optional[Dict[str, Any]]:
        """
        Get job status.
        
        Args:
            job_name: Job name
        
        Returns:
            Job status dict with 'phase', 'succeeded', 'failed', 'active'
            Returns None if job not found
        """
        try:
            job = self.batch_api.read_namespaced_job_status(
                name=job_name,
                namespace=self.namespace
            )
            
            status = {
                "active": job.status.active or 0,
                "succeeded": job.status.succeeded or 0,
                "failed": job.status.failed or 0,
                "start_time": job.status.start_time,
                "completion_time": job.status.completion_time
            }
            
            # Determine phase
            if status["succeeded"] > 0:
                status["phase"] = "Succeeded"
            elif status["failed"] > 0:
                status["phase"] = "Failed"
            elif status["active"] > 0:
                status["phase"] = "Running"
            else:
                status["phase"] = "Pending"
            
            return status
            
        except ApiException as e:
            if e.status == 404:
                return None
            logger.error(f"Error getting job status for {job_name}: {e.status} {e.reason}")
            raise
    
    def delete_job(self, job_name: str, propagation_policy: str = "Background"):
        """
        Delete a job (for cleanup).
        
        Args:
            job_name: Job name
            propagation_policy: How to handle deletion (Background, Foreground, or Orphan)
        """
        try:
            self.batch_api.delete_namespaced_job(
                name=job_name,
                namespace=self.namespace,
                propagation_policy=propagation_policy
            )
            logger.info(f"Deleted job {job_name}")
            
        except ApiException as e:
            if e.status == 404:
                logger.debug(f"Job {job_name} already deleted (404)")
            else:
                logger.error(f"Error deleting job {job_name}: {e.status} {e.reason}")
    
    def get_job_logs(self, job_name: str, tail_lines: int = 50) -> Optional[str]:
        """
        Get logs from a job's pod.
        
        Args:
            job_name: Job name
            tail_lines: Number of lines to retrieve from end
        
        Returns:
            Log output or None if pod not found
        """
        try:
            # Find pod for this job
            pod_list = self.core_api.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=f"job-name={job_name}"
            )
            
            if not pod_list.items:
                logger.warning(f"No pod found for job {job_name}")
                return None
            
            pod_name = pod_list.items[0].metadata.name
            
            # Get logs from pod
            logs = self.core_api.read_namespaced_pod_log(
                name=pod_name,
                namespace=self.namespace,
                tail_lines=tail_lines
            )
            
            return logs
            
        except ApiException as e:
            if e.status == 404:
                return None
            logger.error(f"Error getting logs for job {job_name}: {e.status} {e.reason}")
            return None
    
    def _get_worker_env(self, message_json: str = None) -> list:
        """
        Get environment variables for worker container.
        
        These are copied from the poller pod's environment.
        
        Args:
            message_json: JSON-serialized message body to pass to worker
        """
        env_vars = []
        
        # Pass message body as environment variable
        # This avoids the worker having to re-read from PGMQ (which won't work
        # because the message is invisible due to visibility timeout)
        if message_json:
            env_vars.append(
                client.V1EnvVar(name="MESSAGE_BODY", value=message_json)
            )
        
        # Database connection
        env_vars.extend([
            client.V1EnvVar(
                name="POSTGRES_HOST",
                value=os.environ.get("POSTGRES_HOST", "postgres-primary.gpu-controlplane.svc.cluster.local")
            ),
            client.V1EnvVar(
                name="POSTGRES_PORT",
                value=os.environ.get("POSTGRES_PORT", "5432")
            ),
            client.V1EnvVar(
                name="POSTGRES_USER",
                value=os.environ.get("POSTGRES_USER", "gpudev")
            ),
            client.V1EnvVar(
                name="POSTGRES_DB",
                value=os.environ.get("POSTGRES_DB", "gpudev")
            ),
            client.V1EnvVar(
                name="POSTGRES_PASSWORD",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="postgres-credentials",
                        key="POSTGRES_PASSWORD"
                    )
                )
            ),
        ])
        
        # Queue configuration
        env_vars.append(
            client.V1EnvVar(
                name="QUEUE_NAME",
                value=os.environ.get("QUEUE_NAME", "gpu_reservations")
            )
        )
        
        # AWS configuration (from environment or configmap)
        for env_name in ["REGION", "AWS_DEFAULT_REGION",
                         "EKS_CLUSTER_NAME", "PRIMARY_AVAILABILITY_ZONE",
                         "MAX_RESERVATION_HOURS", "DEFAULT_TIMEOUT_HOURS",
                         "GPU_DEV_CONTAINER_IMAGE", "EFS_SECURITY_GROUP_ID",
                         "EFS_SUBNET_IDS", "CCACHE_SHARED_EFS_ID",
                         "ECR_REPOSITORY_URL", "PROCESSOR_VERSION", "MIN_CLI_VERSION",
                         "IMAGE_PULL_POLICY"]:
            value = os.environ.get(env_name)
            if value:
                env_vars.append(client.V1EnvVar(name=env_name, value=value))
        
        # Kubernetes namespace
        env_vars.append(
            client.V1EnvVar(name="KUBE_NAMESPACE", value=self.namespace)
        )
        
        return env_vars
    
    def list_active_jobs(self) -> list:
        """
        List all active worker jobs.
        
        Returns:
            List of job names that are currently active
        """
        try:
            job_list = self.batch_api.list_namespaced_job(
                namespace=self.namespace,
                label_selector="app=reservation-worker"
            )
            
            active_jobs = []
            for job in job_list.items:
                if job.status.active and job.status.active > 0:
                    active_jobs.append(job.metadata.name)
            
            return active_jobs
            
        except ApiException as e:
            logger.error(f"Error listing active jobs: {e.status} {e.reason}")
            return []

