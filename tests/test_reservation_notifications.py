import copy,json,smtplib,unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import Mock
from reservation_notifications import process,reservation,deliver,build_message,configuration,delivery_status,claim_id
from order_mail import OrderMailSettings
from tests.test_order_mail import base_env

NOW=datetime(2026,11,2,12,tzinfo=timezone.utc)
CUTOFF=NOW-timedelta(hours=1)
ENV=base_env(SSV53_RESERVATION_NOTIFICATIONS_ENABLED='true',SSV53_RESERVATION_NOTIFICATIONS_FROM_UTC=CUTOFF.isoformat(),SSV53_RESERVATION_OFFICE_EMAIL='office@example.test')
DATA={'orderId':'SSV53-261102-113000-ABCD','email':'buyer@example.test','name':'Test Person','status':'reserviert','items':[{'variant':'Frauen – Blau','groupKey':'damen','colorKey':'blau','size':'M','qty':2,'unitPriceCents':1200}],'totalCents':2400}
def row(created=None):
 return {'resultText':'reservierung','resultTitle':DATA['orderId'],'resultMail':DATA['email'],'resultName':DATA['name'],'resultEntry':json.dumps(DATA),'_created':{'$date':(created or NOW-timedelta(minutes=5)).isoformat()}}

class Store:
 def __init__(self):self.values={};self.calls=[]
 def claim(self,*,order_id,recipient):
  self.calls.append(('claim',order_id));status=self.values.get(order_id)
  if status=='sent':return 'sent'
  if status=='sending':return 'busy'
  self.values[order_id]='sending';return 'claimed'
 def mark_sent(self,*,order_id,recipient):self.values[order_id]='sent'
 def mark_failed(self,*,order_id,recipient,error_code):self.values[order_id]='failed'

class SMTP:
 def __init__(self,messages,fail=None):self.messages=messages;self.fail=fail
 def send_message(self,m):
  if self.fail:raise self.fail
  self.messages.append(m);return {}
 def quit(self):pass

class NotificationTests(unittest.TestCase):
 def setUp(self):self.store=Store();self.messages=[]
 def run_scan(self,rows,env=None,smtp_open=None):
  return process(env or ENV,now=NOW,fetch=lambda **kw:rows,store=self.store,smtp_open=smtp_open or (lambda s:SMTP(self.messages)))
 def test_two_independent_mails_use_stored_buyer_and_fixed_office(self):
  self.run_scan([row()]);self.assertEqual([str(m['To']) for m in self.messages],['buyer@example.test','office@example.test'])
  self.assertIn('unverbindliche',self.messages[0].get_body(preferencelist=('plain',)).get_content())
  self.assertIn('2',self.messages[1].get_body(preferencelist=('plain',)).get_content())
  self.assertIsNone(self.messages[0]['Cc']);self.assertIsNone(self.messages[0]['Bcc'])
 def test_repeat_scan_sends_neither_mail_twice(self):
  self.run_scan([row()]);self.run_scan([row()]);self.assertEqual(len(self.messages),2)
 def test_existing_two_orders_and_pre_activation_rows_never_mail(self):
  self.run_scan([row(datetime(2026,9,26,tzinfo=timezone.utc)),row(datetime(2026,10,14,tzinfo=timezone.utc)),row(CUTOFF-timedelta(seconds=1))]);self.assertEqual(self.messages,[]);self.assertEqual(self.store.calls,[])
 def test_disabled_does_not_read_source_or_send(self):
  fetch=Mock();result=process({},fetch=fetch);self.assertFalse(result['enabled']);fetch.assert_not_called()
 def test_edited_client_created_at_does_not_bypass_server_cutoff(self):
  r=row(CUTOFF-timedelta(days=1));d=json.loads(r['resultEntry']);d['createdAt']=NOW.isoformat();r['resultEntry']=json.dumps(d);self.run_scan([r]);self.assertEqual(self.messages,[])
 def test_future_server_timestamp_is_not_mailed(self):
  self.run_scan([row(NOW+timedelta(hours=1))]);self.assertEqual(self.messages,[])
 def test_no_resend_after_retention_removes_old_claim(self):
  old_env=dict(ENV,SSV53_RESERVATION_NOTIFICATIONS_FROM_UTC=(NOW-timedelta(days=200)).isoformat());self.run_scan([row(NOW-timedelta(days=181))],old_env);self.assertEqual(self.messages,[])
 def test_cancelled_reservation_is_not_mailed(self):
  r=row();d=json.loads(r['resultEntry']);d['status']='storniert';r['resultEntry']=json.dumps(d);self.run_scan([r]);self.assertEqual(self.messages,[])
 def test_mismatching_persisted_id_rejected(self):
  r=row();r['resultTitle']='SSV53-261102-113000-WXYZ';self.run_scan([r]);self.assertEqual(self.messages,[])
 def test_customer_failure_does_not_prevent_office_and_retry_only_customer(self):
  n=[0]
  def open_smtp(s):
   n[0]+=1
   if n[0]==1:raise smtplib.SMTPAuthenticationError(535,b'no')
   return SMTP(self.messages)
  self.run_scan([row()],smtp_open=open_smtp);self.assertEqual([str(m['To']) for m in self.messages],['office@example.test'])
  self.run_scan([row()]);self.assertEqual([str(m['To']) for m in self.messages],['office@example.test','buyer@example.test'])
 def test_explicit_rejection_can_retry_but_sent_channel_cannot(self):
  n=[0]
  def open_smtp(s):
   n[0]+=1;return SMTP(self.messages,smtplib.SMTPDataError(451,b'no') if n[0]==1 else None)
  self.run_scan([row()],smtp_open=open_smtp);self.run_scan([row()]);self.assertEqual(len(self.messages),2)
 def test_disconnect_after_send_is_ambiguous_and_not_automatically_retried(self):
  n=[0]
  def open_smtp(s):
   n[0]+=1;return SMTP(self.messages,smtplib.SMTPServerDisconnected() if n[0]==1 else None)
  self.run_scan([row()],smtp_open=open_smtp);self.run_scan([row()]);self.assertEqual(len(self.messages),1);self.assertEqual(self.store.values[claim_id('customer',DATA['orderId'])],'sending')
 def test_original_pickup_enablement_is_not_changed(self):
  self.run_scan([row()]);self.assertEqual(ENV['SSV53_ORDER_MAIL_ENABLED'],'false')
 def test_invalid_fixed_office_recipient_fails_before_sending(self):
  with self.assertRaises(ValueError):self.run_scan([row()],dict(ENV,SSV53_RESERVATION_OFFICE_EMAIL='office@example.test\r\nBcc:other@example.test'))
 def test_admin_message_escapes_stored_name(self):
  d=reservation(row(),CUTOFF,NOW);d['name']='<script>bad</script>';m=build_message(OrderMailSettings.from_mapping(ENV),d,'office','office@example.test');body=m.get_body(preferencelist=('html',)).get_content();self.assertNotIn('<script>',body);self.assertIn('&lt;script&gt;',body)
 def test_status_for_existing_reservation_exposes_no_personal_data(self):
  r=delivery_status(DATA['orderId'],ENV,now=NOW,fetch=lambda oid:[row(CUTOFF-timedelta(days=1))]);self.assertFalse(r['applicable']);self.assertNotIn('buyer@example.test',json.dumps(r))

if __name__=='__main__':unittest.main()
