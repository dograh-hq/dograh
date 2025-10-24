# Telephony Provider Implementation

This module implements the telephony provider abstraction for Dograh AI. For user-facing documentation, see the [Mintlify docs](https://docs.dograh.com/integrations/telephony/overview).

## Architecture

```
Business Logic → TelephonyProvider (Interface) → Concrete Provider (Twilio, Vonage, etc.)
```

## Developer Quick Reference

### Using the Provider in Code

```python
from api.services.telephony.factory import get_telephony_provider

# Get provider based on organization config
provider = await get_telephony_provider(organization_id)

# Initiate a call
result = await provider.initiate_call(
    to_number="+1987654321",
    webhook_url="https://your-app.com/webhook",
    workflow_run_id=123
)
```

## File Structure

```
telephony/
├── __init__.py
├── base.py              # Abstract TelephonyProvider interface
├── factory.py           # Provider creation and config loading
├── providers/
│   ├── __init__.py
│   ├── twilio_provider.py  # Twilio implementation
│   └── vonage_provider.py  # Vonage implementation
├── twilio.py           # Legacy (removed, use factory instead)
└── README.md           # This file
```

## Implementing a New Provider

See the [Custom Provider Guide](https://docs.dograh.com/integrations/telephony/custom) in the documentation for detailed implementation instructions.

Quick checklist:
1. Create `providers/your_provider.py` implementing `TelephonyProvider`
2. Update `factory.py` to include your provider
3. Write unit tests
4. Update documentation

## Key Interfaces

```python
class TelephonyProvider(ABC):
    @abstractmethod
    async def initiate_call(self, to_number: str, webhook_url: str, workflow_run_id: Optional[int] = None, **kwargs: Any) -> Dict[str, Any]
    
    @abstractmethod
    async def get_call_status(self, call_id: str) -> Dict[str, Any]
    
    @abstractmethod
    async def get_available_phone_numbers(self) -> List[str]
    
    @abstractmethod
    def validate_config(self) -> bool
    
    @abstractmethod
    async def verify_webhook_signature(self, url: str, params: Dict[str, Any], signature: str) -> bool
    
    @abstractmethod
    async def get_webhook_response(self, workflow_id: int, user_id: int, workflow_run_id: int) -> str
```

## Configuration Loading

The `factory.py` loads configuration from the database:

**Both Saas and OSS Modes**: Database configuration via UI
   ```python
   # Loaded from organization_configuration table
   key: "TELEPHONY_CONFIGURATION"
   value: {
       "provider": "twilio",  # or "vonage"
       "account_sid": "xxx",  # for Twilio
       "auth_token": "xxx",   # for Twilio
       "application_id": "xxx",  # for Vonage
       "private_key": "xxx",     # for Vonage
       "from_numbers": [...]
   }
   ```

## Testing

### Unit Testing with Mock Provider

```python
class MockProvider(TelephonyProvider):
    async def initiate_call(self, to_number, webhook_url, **kwargs):
        return {"call_id": "mock_123", "status": "initiated"}
    
    async def get_call_status(self, call_id):
        return {"call_id": call_id, "status": "completed"}
    
    # Implement other required methods...

# In tests
@patch('api.services.telephony.factory.get_telephony_provider')
async def test_call_initiation(mock_get_provider):
    mock_get_provider.return_value = MockProvider()
    # Test your business logic
```

### Integration Testing

Run against actual providers in development:

1. Configure your provider through the UI:
   - Navigate to Settings → Integrations → Telephony
   - Select your provider (Twilio or Vonage)
   - Enter test credentials
   - Save configuration

2. Run integration tests:
```bash
pytest tests/integration/test_telephony.py
```

## Migration Notes

### From Direct TwilioService Usage

Old code:
```python
from api.services.telephony.twilio import TwilioService
service = TwilioService(org_id)
await service.initiate_call(...)
```

New code:
```python
from api.services.telephony.factory import get_telephony_provider
provider = await get_telephony_provider(org_id)
await provider.initiate_call(...)
```

### Backward Compatibility

- Old `/api/v1/twilio/*` endpoints still work (redirect to `/api/v1/telephony/*`)
- `TwilioService` class remains for legacy code
- Database configuration key `TWILIO_CONFIGURATION` unchanged

## Common Issues

1. **Import Error**: Always import from `factory`, not directly from providers
2. **Config Not Found**: Check database configuration via UI
3. **Signature Verification**: Ensure auth tokens match between provider and config
4. **WebSocket Issues**: Verify audio format compatibility (MULAW for Twilio)

## Related Documentation

- [User Documentation](https://docs.dograh.com/integrations/telephony/overview)
- [Twilio Integration](https://docs.dograh.com/integrations/telephony/twilio)
- [Custom Providers](https://docs.dograh.com/integrations/telephony/custom)
- [Webhooks Guide](https://docs.dograh.com/integrations/telephony/webhooks)