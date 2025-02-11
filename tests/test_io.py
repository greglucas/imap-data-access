import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import Request

import pytest

import imap_data_access


@pytest.fixture()
def mock_urlopen():
    """Mock urlopen to return a file-like object."""
    mock_data = b"Mock file content"
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = mock_urlopen.return_value.__enter__.return_value
        mock_response.read.return_value = mock_data
        yield mock_urlopen


def _set_mock_data(mock_urlopen, data):
    """Set the data returned by the mock urlopen."""
    mock_response = mock_urlopen.return_value.__enter__.return_value
    mock_response.read.return_value = data


@pytest.mark.parametrize(
    ("file_path", "destination"),
    [
        # Directory structure inferred
        (
            "imap_test_l1_test-description_20100101_20100102_v00-00.cdf",
            "test/l1/2010/01/imap_test_l1_test-description_20100101_20100102_v00-00.cdf",
        ),
        # Directory structure provided in the request
        ("imap/test/config/file.txt", "imap/test/config/file.txt"),
    ],
)
def test_download(mock_urlopen, file_path, destination):
    # Call the download function
    result = imap_data_access.download(file_path)

    # Assert that the file was created
    assert result.exists()
    # Test that the file was saved in the correct location
    expected_destination = imap_data_access.config["DATA_DIR"] / destination
    assert result == expected_destination

    # Assert that the file content matches the mock data
    with open(result, "rb") as f:
        assert f.read() == b"Mock file content"

    # Should have only been one call to urlopen
    mock_urlopen.assert_called_once()

    # Assert that the correct URL was used for the download
    urlopen_calls = mock_urlopen.mock_calls
    # Check the arguments passed to urlopen
    # We pass a Request object, so need to get that with args[0]
    request_sent = urlopen_calls[0].args[0]
    called_url = request_sent.full_url
    # url should be provided as path parameters
    expected_url_encoded = f"https://api.test.com/download/{file_path}"
    assert called_url == expected_url_encoded
    assert request_sent.method == "GET"


def test_download_already_exists(mock_urlopen):
    # Call the download function
    file_path = "a/b.txt"
    # set up the destination and create a file
    destination = Path(imap_data_access.config["DATA_DIR"])
    destination /= f"{file_path}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.touch(exist_ok=True)
    result = imap_data_access.download(file_path)
    assert result == destination
    # Make sure we didn't make any requests
    assert mock_urlopen.call_count == 0


@pytest.mark.parametrize(
    "query_params",
    [
        # All parameters should send full query
        {
            "instrument": "test-instrument",
            "data_level": "test-level",
            "descriptor": "test-description",
            "start_date": "20100101",
            "end_date": "20100102",
            "version": "00-00",
            "extension": "pkts",
        },
        # Make sure not all query params are sent if they are missing
        {"instrument": "test-instrument", "data_level": "test-level"},
    ],
)
def test_query(mock_urlopen, query_params):
    _set_mock_data(mock_urlopen, json.dumps([]).encode("utf-8"))
    response = imap_data_access.query(**query_params)
    # No data found, and JSON decoding works as expected
    assert response == list()

    # Should have only been one call to urlopen
    mock_urlopen.assert_called_once()
    # Assert that the correct URL was used for the query
    urlopen_call = mock_urlopen.mock_calls[0].args[0]
    called_url = urlopen_call.full_url
    expected_url_encoded = f"https://api.test.com/query?{urlencode(query_params)}"
    assert called_url == expected_url_encoded


def test_query_no_params(mock_urlopen):
    with pytest.raises(ValueError, match="At least one query"):
        imap_data_access.query()
    # Should not have made any calls to urlopen
    assert mock_urlopen.call_count == 0


def test_query_bad_params(mock_urlopen):
    with pytest.raises(TypeError, match="got an unexpected"):
        imap_data_access.query(bad_param="test")
    # Should not have made any calls to urlopen
    assert mock_urlopen.call_count == 0


def test_upload_no_file(mock_urlopen):
    path = Path("/non-existant/file.txt")
    assert not path.exists()
    with pytest.raises(FileNotFoundError):
        imap_data_access.upload(path)
    # Should not have made any calls to urlopen
    assert mock_urlopen.call_count == 0


def test_upload_not_relative_to_base(monkeypatch, mock_urlopen):
    # Change the base directory to something else temporarily
    monkeypatch.setitem(imap_data_access.config, "DATA_DIR", Path.cwd() / "/a/b/c")
    with pytest.raises(ValueError, match="File"):
        imap_data_access.upload(Path(__file__))
    assert mock_urlopen.call_count == 0


def test_upload(mock_urlopen):
    _set_mock_data(mock_urlopen, b'"https://s3-test-bucket.com"')
    # Call the upload function
    file_to_upload = imap_data_access.config["DATA_DIR"] / "test-file.txt"
    with open(file_to_upload, "wb") as f:
        f.write(b"test file content")
    assert file_to_upload.exists()
    imap_data_access.upload(file_to_upload)

    # Should have been two calls to urlopen
    # 1. To get the s3 upload url
    # 2. To upload the file to the url returned in 1.
    assert mock_urlopen.call_count == 2

    # We get all returned calls, but we only need the calls
    # where we sent requests
    mock_calls = [
        call
        for call in mock_urlopen.mock_calls
        if len(call.args) and isinstance(call.args[0], Request)
    ]

    # First urlopen call should be to get the s3 upload url
    urlopen_call = mock_calls[0]
    request_sent = urlopen_call.args[0]
    called_url = request_sent.full_url
    expected_url_encoded = "https://api.test.com/upload/science/test-file.txt"
    assert called_url == expected_url_encoded
    assert request_sent.method == "GET"

    # Verify that we put that response into our second request
    urlopen_call = mock_calls[1]
    request_sent = urlopen_call.args[0]
    called_url = request_sent.full_url
    expected_url_encoded = "https://s3-test-bucket.com"
    assert called_url == expected_url_encoded
    assert request_sent.method == "PUT"

    # Assert that the original data from the test file was sent
    assert request_sent.data == b"test file content"
