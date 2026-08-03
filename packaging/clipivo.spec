%global debug_package %{nil}
%global __strip /bin/true
%global __brp_python_bytecompile %{nil}
%global __brp_mangle_shebangs %{nil}
%global _build_id_links none
%global _binary_payload w7.zstdio

Name:           clipivo
Version:        %{clipivo_version}
Release:        %{clipivo_release}%{?dist}
Summary:        Local-first character-aware video cutter
License:        MIT AND LicenseRef-InsightFace-Research-Only
URL:            https://github.com/longweekendlabs/clipivo
Source0:        clipivo-%{version}.tar.zst
Source1:        clipivo-launcher
Source2:        io.longweekendlabs.Clipivo.desktop
Source3:        io.longweekendlabs.Clipivo.svg

BuildArch:      x86_64
AutoReqProv:    no
Requires:       python3.11
Requires:       ffmpeg
Requires:       vlc
Requires:       glib2
Requires:       libsecret
Requires:       libxkbcommon-x11
Requires:       libxcb
Requires:       mesa-libGL
Requires:       mesa-libEGL

%description
Clipivo is a private Long Weekend Labs desktop build for manually and
automatically extracting a selected character's appearances from video. This
package vendors the exact tested Python runtime and local InsightFace buffalo_l
model files. It performs no cloud processing and is not intended for public
redistribution while the model licensing remains unresolved.

%prep
%setup -q

%build

%install
mkdir -p %{buildroot}/opt/clipivo
cp -a . %{buildroot}/opt/clipivo/
install -Dpm 0755 %{SOURCE1} %{buildroot}%{_bindir}/clipivo
install -Dpm 0644 %{SOURCE2} %{buildroot}%{_datadir}/applications/io.longweekendlabs.Clipivo.desktop
install -Dpm 0644 %{SOURCE3} %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.longweekendlabs.Clipivo.svg

%files
%license LICENSE
%doc README.md CHANGELOG.md
/opt/clipivo
%{_bindir}/clipivo
%{_datadir}/applications/io.longweekendlabs.Clipivo.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.longweekendlabs.Clipivo.svg

%changelog
* Fri Jul 24 2026 Long Weekend Labs <2272935+longweekendlabs@users.noreply.github.com> - %{version}-%{release}
- Private local Fedora test build with the Batch export hardening fixes.
