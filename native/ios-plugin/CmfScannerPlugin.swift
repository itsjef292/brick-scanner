import Foundation
import Capacitor
import AVFoundation
import Vision
import UIKit

// Custom Capacitor plugin: a full-screen native scanner that uses the ULTRA-WIDE
// camera with near-focus (macro) — far better at the tiny CMF box Data Matrix
// codes than the regular wide lens — and decodes with Apple's Vision framework
// (no Google ML Kit pods). Registered as "CmfScanner" (see CmfScannerPlugin.m).
//
// JS: const { value } = await CmfScanner.scan();   // value = raw payload, or absent if cancelled
@objc(CmfScannerPlugin)
public class CmfScannerPlugin: CAPPlugin {

    @objc func isAvailable(_ call: CAPPluginCall) {
        call.resolve(["available": true])
    }

    @objc func scan(_ call: CAPPluginCall) {
        let status = AVCaptureDevice.authorizationStatus(for: .video)
        switch status {
        case .denied, .restricted:
            call.reject("Camera permission denied")
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { granted in
                if granted { self.present(call) } else { call.reject("Camera permission denied") }
            }
        default:
            present(call)
        }
    }

    private func present(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            guard let presenter = self.bridge?.viewController else {
                call.reject("No view controller available")
                return
            }
            let vc = CmfScannerViewController()
            vc.modalPresentationStyle = .fullScreen
            vc.onResult = { value in
                DispatchQueue.main.async {
                    presenter.dismiss(animated: true) {
                        if let v = value { call.resolve(["value": v]) }
                        else { call.resolve(["cancelled": true]) }
                    }
                }
            }
            presenter.present(vc, animated: true, completion: nil)
        }
    }
}

final class CmfScannerViewController: UIViewController, AVCaptureVideoDataOutputSampleBufferDelegate {

    var onResult: ((String?) -> Void)?

    private let session = AVCaptureSession()
    private var previewLayer: AVCaptureVideoPreviewLayer?
    private let sampleQueue = DispatchQueue(label: "com.itsjef.brickscanner.cmfscan")
    private var device: AVCaptureDevice?
    private var finished = false
    private var frameCount = 0

    private lazy var barcodeRequest: VNDetectBarcodesRequest = {
        let r = VNDetectBarcodesRequest { [weak self] req, _ in
            guard let self = self, !self.finished else { return }
            guard let results = req.results as? [VNBarcodeObservation] else { return }
            for obs in results where (obs.payloadStringValue?.isEmpty == false) {
                self.handleResult(obs.payloadStringValue!)
                break
            }
        }
        r.symbologies = [.dataMatrix, .qr]
        return r
    }()

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        configureSession()
        addOverlay()
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        if !session.isRunning { sampleQueue.async { self.session.startRunning() } }
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        if session.isRunning { sampleQueue.async { self.session.stopRunning() } }
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        previewLayer?.frame = view.bounds
    }

    // Prefer the ultra-wide lens (focuses much closer → macro), then wide, then default.
    private func bestCamera() -> AVCaptureDevice? {
        if let uw = AVCaptureDevice.default(.builtInUltraWideCamera, for: .video, position: .back) { return uw }
        if let w = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) { return w }
        return AVCaptureDevice.default(for: .video)
    }

    private func configureSession() {
        session.beginConfiguration()
        session.sessionPreset = .hd1920x1080
        guard let cam = bestCamera() else { session.commitConfiguration(); return }
        device = cam
        do {
            let input = try AVCaptureDeviceInput(device: cam)
            if session.canAddInput(input) { session.addInput(input) }
        } catch { session.commitConfiguration(); return }

        let output = AVCaptureVideoDataOutput()
        output.alwaysDiscardsLateVideoFrames = true
        output.setSampleBufferDelegate(self, queue: sampleQueue)
        if session.canAddOutput(output) { session.addOutput(output) }
        session.commitConfiguration()

        // Macro tuning: continuous AF restricted to the near range, and a modest
        // zoom so the ultra-wide's very wide field isn't too small to aim with.
        do {
            try cam.lockForConfiguration()
            if cam.isFocusModeSupported(.continuousAutoFocus) { cam.focusMode = .continuousAutoFocus }
            if cam.isAutoFocusRangeRestrictionSupported { cam.autoFocusRangeRestriction = .near }
            let zoom = min(CGFloat(2.0), cam.activeFormat.videoMaxZoomFactor)
            cam.videoZoomFactor = max(1.0, zoom)
            cam.unlockForConfiguration()
        } catch {}

        let pl = AVCaptureVideoPreviewLayer(session: session)
        pl.videoGravity = .resizeAspectFill
        pl.frame = view.bounds
        view.layer.insertSublayer(pl, at: 0)
        previewLayer = pl
    }

    private func addOverlay() {
        // Reticle
        let reticle = UIView()
        reticle.translatesAutoresizingMaskIntoConstraints = false
        reticle.backgroundColor = .clear
        reticle.layer.borderColor = UIColor.white.withAlphaComponent(0.85).cgColor
        reticle.layer.borderWidth = 2
        reticle.layer.cornerRadius = 14
        view.addSubview(reticle)
        let side = min(view.bounds.width, view.bounds.height) * 0.62
        NSLayoutConstraint.activate([
            reticle.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            reticle.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            reticle.widthAnchor.constraint(equalToConstant: side),
            reticle.heightAnchor.constraint(equalToConstant: side)
        ])

        // Hint
        let hint = PaddedLabel()
        hint.translatesAutoresizingMaskIntoConstraints = false
        hint.text = "Point at the small square 2D code on the box — get close, hold steady"
        hint.textColor = .white
        hint.font = .systemFont(ofSize: 14, weight: .medium)
        hint.numberOfLines = 0
        hint.textAlignment = .center
        hint.backgroundColor = UIColor.black.withAlphaComponent(0.55)
        hint.layer.cornerRadius = 10
        hint.layer.masksToBounds = true
        view.addSubview(hint)
        NSLayoutConstraint.activate([
            hint.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 20),
            hint.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -20),
            hint.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -24)
        ])

        view.addSubview(pillButton("Cancel", action: #selector(cancelTapped), leading: true))
        view.addSubview(pillButton("Light", action: #selector(torchTapped), leading: false))
    }

    private func pillButton(_ title: String, action: Selector, leading: Bool) -> UIButton {
        let b = UIButton(type: .system)
        b.translatesAutoresizingMaskIntoConstraints = false
        b.setTitle(title, for: .normal)
        b.setTitleColor(.white, for: .normal)
        b.titleLabel?.font = .systemFont(ofSize: 16, weight: .semibold)
        b.backgroundColor = UIColor.black.withAlphaComponent(0.5)
        b.layer.cornerRadius = 18
        b.contentEdgeInsets = UIEdgeInsets(top: 8, left: 16, bottom: 8, right: 16)
        b.addTarget(self, action: action, for: .touchUpInside)
        // constrain after adding to superview
        DispatchQueue.main.async {
            b.topAnchor.constraint(equalTo: self.view.safeAreaLayoutGuide.topAnchor, constant: 12).isActive = true
            if leading {
                b.leadingAnchor.constraint(equalTo: self.view.leadingAnchor, constant: 16).isActive = true
            } else {
                b.trailingAnchor.constraint(equalTo: self.view.trailingAnchor, constant: -16).isActive = true
            }
        }
        return b
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        if finished { return }
        frameCount += 1
        if frameCount % 2 != 0 { return }   // every other frame is plenty
        guard let pb = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let handler = VNImageRequestHandler(cvPixelBuffer: pb, orientation: .right, options: [:])
        try? handler.perform([barcodeRequest])
    }

    private func handleResult(_ payload: String) {
        if finished { return }
        finished = true
        DispatchQueue.main.async {
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            self.onResult?(payload)
        }
    }

    @objc private func cancelTapped() {
        if finished { return }
        finished = true
        onResult?(nil)
    }

    @objc private func torchTapped() {
        guard let d = device, d.hasTorch else { return }
        do {
            try d.lockForConfiguration()
            d.torchMode = (d.torchMode == .on) ? .off : .on
            d.unlockForConfiguration()
        } catch {}
    }
}

// UILabel with internal padding (so the hint pill isn't text-to-edge).
final class PaddedLabel: UILabel {
    private let inset = UIEdgeInsets(top: 9, left: 13, bottom: 9, right: 13)
    override func drawText(in rect: CGRect) { super.drawText(in: rect.inset(by: inset)) }
    override var intrinsicContentSize: CGSize {
        let s = super.intrinsicContentSize
        return CGSize(width: s.width + inset.left + inset.right, height: s.height + inset.top + inset.bottom)
    }
}
