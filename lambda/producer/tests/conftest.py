import os
import sys
import json
from unittest.mock import patch, MagicMock

os.environ.setdefault('DYNAMODB_TABLE', 'test-inventory')
os.environ.setdefault('GCP_PROJECT_ID', 'test-project')
os.environ.setdefault('BQ_DATASET_RAW', 'test_raw')
os.environ.setdefault('GCP_KEY_PARAM', '/test/gcp-key')

# Inject fake Google modules — google-cloud-bigquery is not installed in the test env.
# Python resolves "from google.cloud import bigquery" against sys.modules first,
# so injecting MagicMocks here prevents any real import attempt.
for _mod in ['google', 'google.cloud', 'google.cloud.bigquery',
             'google.oauth2', 'google.oauth2.service_account']:
    sys.modules.setdefault(_mod, MagicMock())

_FAKE_GCP_KEY = json.dumps({
    "type": "service_account",
    "project_id": "test-project",
    "private_key_id": "fake-key",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n",
    "client_email": "test@test-project.iam.gserviceaccount.com",
    "client_id": "123",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
})

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_mock_ssm = MagicMock()
_mock_ssm.get_parameter.return_value = {'Parameter': {'Value': _FAKE_GCP_KEY}}

_p1 = patch('boto3.client', return_value=_mock_ssm)
_p2 = patch('boto3.resource')

_p1.start()
_p2.start()

import producer_lambda  # noqa: E402  module initialises with mocks active

_p1.stop()
_p2.stop()