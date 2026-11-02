"""Offline checks for preserving the live Flex app during the mail rollout."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('notification_deploy',
    Path(__file__).parents[1] / 'scripts/deploy_reservation_notifications.py')
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def response(status=200, body=None, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = body or {}
    return result


class SettingsTests(TestCase):
    def test_only_notification_values_change_and_other_settings_stay_in_memory(self):
        existing = {'SMTP_PASSWORD': 'synthetic-credential', 'MOWER_MODE': 'unchanged',
                    'SSV53_ORDER_MAIL_ENABLED': 'true'}
        changes = {'SSV53_RESERVATION_NOTIFICATIONS_ENABLED': 'false'}
        session = Mock()
        session.request.side_effect = [
            response(body={'properties': existing}, headers={'ETag': 'version-one'}),
            response(), response(body={'properties': dict(existing, **changes)})]
        with patch('builtins.print') as output:
            deploy.feature_settings(session, 'https://management.azure.com/site', changes)
        write = session.request.call_args_list[1]
        self.assertEqual(write.args[0], 'PUT')
        self.assertEqual(write.kwargs['json'], {'properties': dict(existing, **changes)})
        self.assertEqual(write.kwargs['headers'], {'If-Match': 'version-one'})
        self.assertNotIn('SSV53_RESERVATION_NOTIFICATIONS_ENABLED', existing)
        self.assertNotIn('synthetic-credential', str(output.call_args_list))
        for request in session.request.call_args_list:
            self.assertTrue(request.args[1].endswith('?api-version=2024-04-01'))
            self.assertFalse(request.kwargs['allow_redirects'])

    def test_refuses_unrelated_settings_before_any_cloud_call(self):
        session = Mock()
        with self.assertRaises(ValueError):
            deploy.feature_settings(session, 'https://management.azure.com/site',
                                    {'SSV53_ORDER_MAIL_ENABLED': 'false'})
        session.request.assert_not_called()

    def test_invalid_settings_response_cannot_replace_current_configuration(self):
        session = Mock()
        session.request.return_value = response(body={'properties': None})
        with self.assertRaises(RuntimeError):
            deploy.feature_settings(session, 'https://management.azure.com/site',
                                    {'SSV53_RESERVATION_NOTIFICATIONS_ENABLED': 'false'})
        self.assertEqual(session.request.call_count, 1)

    def test_cloud_errors_never_expose_response_content(self):
        session = Mock()
        session.request.return_value = response(403, {'secret': 'must-not-be-printed'})
        with self.assertRaisesRegex(RuntimeError, 'HTTP 403') as error:
            deploy.arm(session, 'https://management.azure.com/site', 'POST',
                       '/config/appsettings/list')
        self.assertNotIn('must-not-be-printed', str(error.exception))


class FlexPublishTests(TestCase):
    def test_acceptance_and_transient_404_are_not_treated_as_completion(self):
        session = Mock()
        session.post.return_value = response(202)
        session.get.side_effect = [response(404), response(202),
                                   response(body={'status': 4})]
        with TemporaryDirectory() as folder:
            package = Path(folder) / 'active-patched.zip'
            package.write_bytes(b'existing-built-dependencies-and-patched-source')
            with patch.object(deploy.time, 'sleep'), patch('builtins.print'):
                deploy.deploy_flex(session, package)
        self.assertEqual(session.get.call_count, 3)
        self.assertEqual(session.post.call_args.kwargs['params']['RemoteBuild'], 'false')

    def test_failed_and_cancelled_publications_are_not_reported_as_success(self):
        for status in (-1, 3, 5, 6):
            with self.subTest(status=status), TemporaryDirectory() as folder:
                session = Mock()
                session.post.return_value = response(202)
                session.get.return_value = response(body={'status': status})
                package = Path(folder) / 'package.zip'
                package.write_bytes(b'package')
                with patch('builtins.print'), self.assertRaises(RuntimeError):
                    deploy.deploy_flex(session, package)

    def test_publish_conflict_does_not_poll_another_deployment(self):
        session = Mock()
        session.post.return_value = response(409)
        with TemporaryDirectory() as folder:
            package = Path(folder) / 'package.zip'
            package.write_bytes(b'package')
            with self.assertRaisesRegex(RuntimeError, 'HTTP 409'):
                deploy.deploy_flex(session, package)
        session.get.assert_not_called()
