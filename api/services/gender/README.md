# Gender Prediction Service

An internal service for predicting gender from first names using SSA (Social Security Administration) baby names data with GenderAPI fallback for uncertain predictions.

## Overview

This service provides gender prediction with:
- **Local model** built from 145 years of SSA data (1880-2024)
- **104,819 unique names** with confidence scores
- **Compressed storage** (2.21 MB model file)
- **GenderAPI fallback** for unknown or low-confidence names

## Data Source

The SSA baby names data is already downloaded in the `names/` directory from:
https://catalog.data.gov/dataset/baby-names-from-social-security-card-applications-national-data

## Building the Model

### Prerequisites
- Python 3.11+
- SSA data files in `names/` directory (already included)

### Build Steps

```bash
# Navigate to the gender service directory
cd dograh/api/services/gender/

# Run the model builder
python build_model.py
```

This will:
1. Process all 145 year files (yob1880.txt to yob2024.txt)
2. Aggregate name counts across all years
3. Calculate confidence scores based on gender ratios
4. Generate a compressed `model.txt` file (~2.21 MB)

### Model Output

The builder generates `model.txt` with:
- **Version**: Model version number
- **Metadata**: Build date, statistics, thresholds
- **Names**: Compressed array format `[male_count, female_count, confidence]`

Example output:
```
Model saved to: .../services/gender/model.txt
File size: 2.21 MB

Model statistics:
  Total names: 104,819
  High confidence names (≥0.85): 1,711
  Confidence percentage: 1.6%
```

## Using the Service

### Basic Usage

```python
from services.gender.gender_service import GenderService

# Initialize the service
service = GenderService()

# Predict gender for a single name
result = await service.predict("John")
print(f"Gender: {result.gender}")  # "male"
print(f"Confidence: {result.confidence}")  # 0.996
print(f"Source: {result.source}")  # "model"

# Get salutation for a name
greeting = await service.get_salutation("John")
print(f"Salutation: {greeting}")  # "Mr."

greeting = await service.get_salutation("Mary")
print(f"Salutation: {greeting}")  # "Ms."

greeting = await service.get_salutation("Unknown")
print(f"Salutation: {greeting}")  # "Dear"

# Clean up
await service.close()
```

### Configuration Options

```python
# Custom configuration
service = GenderService(
    model_path="custom/path/to/model.txt",  # Default: ./model.txt
    confidence_threshold=0.85,  # Default: 0.85
    gender_api_key="your-api-key",  # Default: from GENDER_API_KEY env
    gender_api_url="https://...",  # Default: GenderAPI v2 endpoint
)
```

### Salutation Generation

```python
# Get appropriate salutation based on gender
salutation = await service.get_salutation("John")  # "Mr."
salutation = await service.get_salutation("Mary")  # "Ms."
salutation = await service.get_salutation("Unknown")  # "Dear"

# Custom confidence threshold for salutation
salutation = await service.get_salutation(
    "Taylor",  # Ambiguous name
    confidence_threshold=0.9,  # Higher threshold
)  # Returns "Dear" due to low confidence

# Salutation logic:
# - "Mr." for male with confidence >= threshold
# - "Ms." for female with confidence >= threshold
# - "Dear" for unknown gender or low confidence
```

### Batch Predictions

```python
# Predict multiple names at once
names = ["Alice", "Bob", "Charlie", "Diana"]
results = await service.batch_predict(names)

for name, result in zip(names, results):
    print(f"{name}: {result.gender} ({result.confidence:.2f})")
```

### Response Format

```python
class GenderPrediction:
    gender: "male" | "female" | "unknown"  # Predicted gender
    confidence: float  # 0.0 to 1.0
    source: "model" | "genderapi"  # Prediction source
```

### Service Statistics

```python
# Get service statistics
stats = await service.get_stats()
print(f"Total names: {stats['model']['total_names']:,}")
print(f"High confidence: {stats['model']['high_confidence_names']:,}")
print(f"Cached names in Redis: {stats['cache']['cached_names']}")
print(f"Cache TTL: {stats['cache']['ttl_seconds']} seconds")
print(f"API enabled: {stats['api']['enabled']}")
```

### Cache Management

```python
# Clear Redis cache
await service.clear_cache()
```

## Environment Variables

```bash
# Required: Redis connection URL
export REDIS_URL=redis://localhost:6379

# Optional: Set GenderAPI key for fallback
export GENDERAPI_API_KEY=your-api-key-here

# Optional: Override confidence threshold (default: 0.85)
export CONFIDENCE_THRESHOLD=0.85
```

## How It Works

1. **Name normalization**: Converts to lowercase, strips whitespace
2. **Local model check**: Looks up name in pre-built model
3. **Confidence evaluation**: If confidence ≥ 0.85, returns local prediction
4. **Redis cache check**: Checks Redis for previously fetched API results
5. **API fallback**: For unknown/low-confidence names, calls GenderAPI
6. **Redis caching**: Stores API responses in Redis with 30-day TTL

## Testing

Run the test suite to verify the service:

```bash
python test_service.py
```

This tests:
- High-confidence predictions
- Ambiguous names
- Edge cases (empty strings, special characters)
- International names (with API key)
- Batch predictions

## Model Updates

The model should be rebuilt annually when new SSA data is released:

1. Download new year file (e.g., yob2025.txt) to `names/` directory
2. Run `python build_model.py` to rebuild
3. Test with `python test_service.py`
4. Commit the updated `model.txt`

## Performance

- **Model size**: 2.21 MB (compressed JSON)
- **Load time**: < 100ms
- **Prediction time**: < 1ms (local), < 5ms (Redis cache), < 500ms (API)
- **Memory usage**: ~10 MB for model in memory
- **Cache**: Redis-based with 30-day TTL
- **Scalability**: Shared cache across all service instances

## Limitations

- Based on US SSA data (may not work well for non-US names)
- Historical bias in older data
- Unisex names have lower confidence
- Requires GenderAPI key for comprehensive coverage
