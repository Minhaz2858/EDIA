# Bepsbot - An Intelligent Comment Analysis and Enhancement System

## Overview

Bepsbot is a sophisticated NLP application designed to analyze and enhance user comments by predicting supportive communication metrics and suggesting improvements. It leverages state-of-the-art transformers and generative AI to provide intelligent feedback and recommendations.

## Features

- **Comment Analysis**: Analyzes comments for Instrumental Support (IS) and Emotional Support (ES) scores
- **Generative Recommendations**: Suggests improved versions of comments with specific enhancements
- **Safety Filtering**: Ensures comments meet safety standards before processing
- **Vector Database**: RAG-based retrieval of similar high-quality comments for context
- **Multi-Modal Support**: Supports both synchronous and asynchronous processing

## Tech Stack

- **Backend**: FastAPI, Flask
- **NLP Models**: RoBERTa, Sentence Transformers, BERT
- **Databases**: Elasticsearch, ChromaDB
- **Language Models**: OpenAI, DeepSeek
- **Utilities**: NLTK, TextBlob, spaCy, LIWC

## Project Structure

```
├── app.py                 # Flask application (frontend API)
├── backend_api.py         # FastAPI backend with ML models
├── models/
│   ├── transformer_predictor.py  # RoBERTa prediction wrapper
│   ├── generative_recommender.py # LLM-based comment enhancement
│   ├── safety_filter.py          # Content safety checking
│   ├── roberta_is/               # IS model
│   └── roberta_es/               # ES model
├── static/                # Frontend assets (CSS, JS)
├── templates/             # HTML templates
├── training/              # Training scripts and data pipeline
├── util.py                # Utility functions
├── index.py               # Elasticsearch indexing
├── requirements.txt       # Python dependencies
└── environment.yml        # Conda environment specification
```

## Installation

### Prerequisites

- Python 3.10+
- Conda (optional, for environment management)
- Elasticsearch instance (for document indexing)

### Setup

1. **Clone the repository**

   ```bash
   git clone <repository-url>
   cd bepsbot
   ```

2. **Create environment variables**

   ```bash
   cp .env.example .env
   # Edit .env with your actual credentials
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

   Or using Conda:

   ```bash
   conda env create -f environment.yml
   conda activate bepsbot_new
   ```

4. **Set up Elasticsearch**
   - Ensure Elasticsearch is running
   - Update `.env` with Elasticsearch credentials and CA certificate path

5. **Initialize the database**

   ```bash
   python build_vector_db.py
   python index.py
   ```

## Environment Variables

Required environment variables (see `.env.example`):

- `OPENAI_API_KEY`: Your OpenAI API key
- `DEEPSEEK_API_KEY`: Your DeepSeek API key
- `ELASTICSEARCH_HOST`: Elasticsearch server URL
- `ELASTIC_USERNAME`: Elasticsearch username
- `ELASTIC_PASSWORD`: Elasticsearch password
- `ELASTICSEARCH_CA_CERTS_PATH`: Path to CA certificate for Elasticsearch

## Running the Application

### Start the Backend API

```bash
python backend_api.py
```

The FastAPI server will run on `http://localhost:8000`

### Start the Flask Frontend

```bash
python app.py
```

The Flask server will run on `http://localhost:5000`

## API Endpoints

### Backend API (FastAPI)

- `POST /predict_scores`: Predict IS/ES scores for a comment
- `POST /recommend_candidates`: Generate improved comment versions with safety checks

### Frontend API (Flask)

- `/`: Main application interface
- `/api/analyze`: Analyze user comments
- `/api/polish`: Polish comments with specific focus areas
- `/api/assess`: Comprehensive comment assessment

## Usage Examples

### Analyze a Comment

```python
import requests

response = requests.post(
    "http://localhost:8000/predict_scores",
    json={"comment": "This is a supportive comment"}
)
print(response.json())
```

### Get Recommendations

```python
response = requests.post(
    "http://localhost:8000/recommend_candidates",
    json={
        "op_text": "Original post text",
        "comment": "User comment to enhance"
    }
)
print(response.json())
```

## Training

To retrain models with new data:

```bash
python training/data_cleaning_pipeline.py
# This will process user-submitted comments and create training data

python training/train_models.py  # (if available)
```

## Data Processing Pipeline

1. **Record Collection**: User comments are stored in `record/` directory
2. **Safety Filtering**: Comments are checked for safety compliance
3. **Quality Filtering**: Length and quality checks
4. **Pseudo-Labeling**: Existing models predict scores
5. **CSV Export**: Data saved for model retraining

## Contributing

1. Create a feature branch
2. Commit changes
3. Push to the branch
4. Create a Pull Request

## Security & Privacy

- **API Keys**: Never commit actual API keys. Use environment variables
- **Elasticsearch Credentials**: Use `.env` file (gitignored)
- **User Data**: Records directory contains user submissions. Review before committing
- **Database Files**: ChromaDB and Elasticsearch data are excluded from git

## Troubleshooting

### Elasticsearch Connection Failed

- Verify Elasticsearch is running
- Check credentials in `.env`
- Ensure CA certificate path is correct

### Model Loading Issues

- Verify `models/roberta_is/` and `models/roberta_es/` directories exist
- Check model file integrity

### API Key Errors

- Confirm environment variables are set: `echo $DEEPSEEK_API_KEY`
- Regenerate API keys if compromised

## License

[Specify your license here]

## Support

For issues and questions, please open an issue on GitHub.

---

**Note**: This project requires proper API credentials and Elasticsearch setup. Ensure all sensitive data is stored in `.env` and never committed to version control.
